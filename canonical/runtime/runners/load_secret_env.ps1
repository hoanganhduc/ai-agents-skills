function Get-AasSecretItemWithoutReparse {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LiteralPath
    )

    if (-not (Test-Path -LiteralPath $LiteralPath -PathType Leaf)) {
        throw "Secret env file does not exist"
    }
    $item = Get-Item -LiteralPath $LiteralPath -Force
    $cursor = $item
    while ($null -ne $cursor) {
        if (($cursor.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Secret env path contains a reparse point"
        }
        if ($cursor -is [System.IO.DirectoryInfo]) {
            $cursor = $cursor.Parent
        } else {
            $cursor = $cursor.Directory
        }
    }
    return $item
}


function Skip-AasJsonWhitespace {
    param([Parameter(Mandatory = $true)][object]$State)
    while ($State.Index -lt $State.Length) {
        $code = [int][char]$State.Text[$State.Index]
        if ($code -notin @(0x20, 0x09, 0x0A, 0x0D)) {
            break
        }
        $State.Index += 1
    }
}


function Read-AasJsonString {
    param([Parameter(Mandatory = $true)][object]$State)
    if (
        $State.Index -ge $State.Length -or
        [char]$State.Text[$State.Index] -ne '"'
    ) {
        throw "Secret JSON expected a string"
    }
    $State.Index += 1
    $builder = [System.Text.StringBuilder]::new()
    while ($State.Index -lt $State.Length) {
        $character = [char]$State.Text[$State.Index]
        $State.Index += 1
        if ($character -eq '"') {
            return $builder.ToString()
        }
        if ([int]$character -lt 0x20) {
            throw "Secret JSON string contains a control character"
        }
        if ($character -ne '\') {
            [void]$builder.Append($character)
            continue
        }
        if ($State.Index -ge $State.Length) {
            throw "Secret JSON contains an incomplete escape"
        }
        $escape = [char]$State.Text[$State.Index]
        $State.Index += 1
        switch ($escape) {
            '"' { [void]$builder.Append('"') }
            '\' { [void]$builder.Append('\') }
            '/' { [void]$builder.Append('/') }
            'b' { [void]$builder.Append([char]0x08) }
            'f' { [void]$builder.Append([char]0x0C) }
            'n' { [void]$builder.Append([char]0x0A) }
            'r' { [void]$builder.Append([char]0x0D) }
            't' { [void]$builder.Append([char]0x09) }
            'u' {
                if ($State.Index + 4 -gt $State.Length) {
                    throw "Secret JSON contains an incomplete Unicode escape"
                }
                $hex = $State.Text.Substring($State.Index, 4)
                if ($hex -cnotmatch '^[0-9A-Fa-f]{4}$') {
                    throw "Secret JSON contains an invalid Unicode escape"
                }
                [void]$builder.Append([char][Convert]::ToInt32($hex, 16))
                $State.Index += 4
            }
            default { throw "Secret JSON contains an unsupported escape" }
        }
    }
    throw "Secret JSON contains an unterminated string"
}


function ConvertFrom-AasStrictFlatJson {
    param([Parameter(Mandatory = $true)][string]$Text)
    $state = [pscustomobject]@{
        Text = $Text
        Index = 0
        Length = $Text.Length
    }
    $values = [System.Collections.Generic.Dictionary[string,string]]::new(
        [System.StringComparer]::Ordinal
    )
    Skip-AasJsonWhitespace -State $state
    if (
        $state.Index -ge $state.Length -or
        [char]$state.Text[$state.Index] -ne '{'
    ) {
        throw "Secret JSON must contain one object"
    }
    $state.Index += 1
    Skip-AasJsonWhitespace -State $state
    if (
        $state.Index -lt $state.Length -and
        [char]$state.Text[$state.Index] -eq '}'
    ) {
        $state.Index += 1
    } else {
        while ($true) {
            Skip-AasJsonWhitespace -State $state
            $key = Read-AasJsonString -State $state
            Skip-AasJsonWhitespace -State $state
            if (
                $state.Index -ge $state.Length -or
                [char]$state.Text[$state.Index] -ne ':'
            ) {
                throw "Secret JSON property is missing ':'"
            }
            $state.Index += 1
            Skip-AasJsonWhitespace -State $state
            $value = Read-AasJsonString -State $state
            if ($values.ContainsKey($key)) {
                throw "Secret JSON contains a duplicate key"
            }
            $values.Add($key, $value)
            Skip-AasJsonWhitespace -State $state
            if ($state.Index -ge $state.Length) {
                throw "Secret JSON object is incomplete"
            }
            $delimiter = [char]$state.Text[$state.Index]
            $state.Index += 1
            if ($delimiter -eq '}') {
                break
            }
            if ($delimiter -ne ',') {
                throw "Secret JSON expected ',' or '}'"
            }
        }
    }
    Skip-AasJsonWhitespace -State $state
    if ($state.Index -ne $state.Length) {
        throw "Secret JSON has trailing content"
    }
    return $values
}


function Import-AasSecretEnvFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PointerEnv,

        [Parameter(Mandatory = $true)]
        [string[]]$AllowedKeys,

        [string[]]$ExportKeys = @(),

        [switch]$ExportSubset,

        [ValidateSet("env", "json")]
        [string]$Format = "env",

        [switch]$RetainPointer,

        [switch]$ValidateOnly
    )

    $allowed = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    foreach ($key in $AllowedKeys) {
        [void]$allowed.Add($key)
    }
    $selected = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    $subsetMode = $ExportSubset.IsPresent -or $PSBoundParameters.ContainsKey("ExportKeys")
    if ($subsetMode) {
        foreach ($key in $ExportKeys) {
            if (-not $allowed.Contains($key)) {
                throw "Secret env export keys must be a subset of allowed keys"
            }
            [void]$selected.Add($key)
        }
    } else {
        foreach ($key in $AllowedKeys) {
            [void]$selected.Add($key)
        }
    }

    $pathValue = [Environment]::GetEnvironmentVariable($PointerEnv, "Process")
    if ([string]::IsNullOrEmpty($pathValue)) {
        if ($subsetMode) {
            foreach ($key in $AllowedKeys) {
                [Environment]::SetEnvironmentVariable(
                    $key,
                    $null,
                    [System.EnvironmentVariableTarget]::Process
                )
            }
            if (-not $RetainPointer.IsPresent) {
                [Environment]::SetEnvironmentVariable(
                    $PointerEnv,
                    $null,
                    [System.EnvironmentVariableTarget]::Process
                )
            }
        }
        return
    }
    if ($pathValue -ne $pathValue.Trim()) {
        throw "$PointerEnv has surrounding whitespace"
    }
    if (-not [System.IO.Path]::IsPathRooted($pathValue)) {
        throw "$PointerEnv must name an absolute path"
    }

    $absolute = [System.IO.Path]::GetFullPath($pathValue)
    [void](Get-AasSecretItemWithoutReparse -LiteralPath $absolute)

    if ($null -eq ("AasSecretFile.NativeMethods" -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace AasSecretFile {
    public sealed class Snapshot {
        public byte[] Bytes { get; set; }
        public string SecurityDescriptor { get; set; }
        public string Stability { get; set; }
        public uint LinkCount { get; set; }
    }

    public static class NativeMethods {
        [StructLayout(LayoutKind.Sequential)]
        private struct ByHandleFileInformation {
            public uint FileAttributes;
            public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime;
            public System.Runtime.InteropServices.ComTypes.FILETIME LastAccessTime;
            public System.Runtime.InteropServices.ComTypes.FILETIME LastWriteTime;
            public uint VolumeSerialNumber;
            public uint FileSizeHigh;
            public uint FileSizeLow;
            public uint NumberOfLinks;
            public uint FileIndexHigh;
            public uint FileIndexLow;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct FileBasicInformation {
            public long CreationTime;
            public long LastAccessTime;
            public long LastWriteTime;
            public long ChangeTime;
            public uint FileAttributes;
        }

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetFileInformationByHandle(
            SafeFileHandle handle,
            out ByHandleFileInformation information
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetFileInformationByHandleEx(
            SafeFileHandle handle,
            int fileInformationClass,
            out FileBasicInformation information,
            uint bufferSize
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool ReadFile(
            SafeFileHandle handle,
            byte[] buffer,
            uint bytesToRead,
            out uint bytesRead,
            IntPtr overlapped
        );

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateFileW(
            string fileName,
            uint desiredAccess,
            uint shareMode,
            IntPtr securityAttributes,
            uint creationDisposition,
            uint flagsAndAttributes,
            IntPtr templateFile
        );

        [DllImport("advapi32.dll", SetLastError = true)]
        private static extern uint GetSecurityInfo(
            SafeFileHandle handle,
            uint objectType,
            uint securityInformation,
            out IntPtr owner,
            out IntPtr group,
            out IntPtr dacl,
            out IntPtr sacl,
            out IntPtr securityDescriptor
        );

        [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool ConvertSecurityDescriptorToStringSecurityDescriptorW(
            IntPtr securityDescriptor,
            uint requestedStringSDRevision,
            uint securityInformation,
            out IntPtr stringSecurityDescriptor,
            out uint stringSecurityDescriptorLength
        );

        [DllImport("kernel32.dll")]
        private static extern IntPtr LocalFree(IntPtr memory);

        private const uint GenericRead = 0x80000000;
        private const uint ReadControl = 0x00020000;
        private const uint FileReadAttributes = 0x00000080;
        private const uint FileShareRead = 0x00000001;
        private const uint OpenExisting = 3;
        private const uint FileFlagOpenReparsePoint = 0x00200000;
        private const uint FileFlagBackupSemantics = 0x02000000;
        private const uint FileAttributeDirectory = 0x00000010;
        private const uint FileAttributeReparsePoint = 0x00000400;
        private const uint SeFileObject = 1;
        private const uint OwnerSecurityInformation = 0x00000001;
        private const uint DaclSecurityInformation = 0x00000004;

        private static SafeFileHandle OpenNoFollow(string path, bool directory) {
            uint access = ReadControl | FileReadAttributes;
            if (!directory) {
                access |= GenericRead;
            }
            uint flags = FileFlagOpenReparsePoint;
            if (directory) {
                flags |= FileFlagBackupSemantics;
            }
            SafeFileHandle handle = CreateFileW(
                path,
                access,
                FileShareRead,
                IntPtr.Zero,
                OpenExisting,
                flags,
                IntPtr.Zero
            );
            if (handle.IsInvalid) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            return handle;
        }

        private static ByHandleFileInformation GetIdentity(SafeFileHandle handle) {
            ByHandleFileInformation identity;
            if (!GetFileInformationByHandle(handle, out identity)) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            return identity;
        }

        private static string GetDescriptor(SafeFileHandle handle) {
            IntPtr owner;
            IntPtr group;
            IntPtr dacl;
            IntPtr sacl;
            IntPtr descriptor;
            uint requested = OwnerSecurityInformation | DaclSecurityInformation;
            uint result = GetSecurityInfo(
                handle,
                SeFileObject,
                requested,
                out owner,
                out group,
                out dacl,
                out sacl,
                out descriptor
            );
            if (result != 0) {
                throw new Win32Exception((int)result);
            }
            IntPtr text = IntPtr.Zero;
            try {
                if (owner == IntPtr.Zero || dacl == IntPtr.Zero) {
                    throw new InvalidDataException("owner or DACL is absent");
                }
                uint textLength;
                if (!ConvertSecurityDescriptorToStringSecurityDescriptorW(
                    descriptor,
                    1,
                    requested,
                    out text,
                    out textLength
                )) {
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                }
                return Marshal.PtrToStringUni(text);
            } finally {
                if (text != IntPtr.Zero) {
                    LocalFree(text);
                }
                if (descriptor != IntPtr.Zero) {
                    LocalFree(descriptor);
                }
            }
        }

        private static string Stability(
            SafeFileHandle handle,
            ByHandleFileInformation identity
        ) {
            FileBasicInformation basic;
            if (!GetFileInformationByHandleEx(
                handle,
                0,
                out basic,
                (uint)Marshal.SizeOf(typeof(FileBasicInformation))
            )) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            ulong size = ((ulong)identity.FileSizeHigh << 32) | identity.FileSizeLow;
            return String.Join(":", new string[] {
                identity.VolumeSerialNumber.ToString(),
                identity.FileIndexHigh.ToString(),
                identity.FileIndexLow.ToString(),
                size.ToString(),
                identity.LastWriteTime.dwHighDateTime.ToString(),
                identity.LastWriteTime.dwLowDateTime.ToString(),
                basic.ChangeTime.ToString(),
                identity.NumberOfLinks.ToString()
            });
        }

        public static Snapshot ReadSnapshot(string path, int maximumBytes) {
            using (SafeFileHandle handle = OpenNoFollow(path, false)) {
                ByHandleFileInformation before = GetIdentity(handle);
                if ((before.FileAttributes & FileAttributeReparsePoint) != 0 ||
                    (before.FileAttributes & FileAttributeDirectory) != 0) {
                    throw new InvalidDataException("secret path is a reparse point or directory");
                }
                string beforeStability = Stability(handle, before);
                string beforeDescriptor = GetDescriptor(handle);
                byte[] bytes;
                using (MemoryStream output = new MemoryStream()) {
                    byte[] buffer = new byte[4096];
                    while (true) {
                        uint read;
                        if (!ReadFile(
                            handle,
                            buffer,
                            (uint)buffer.Length,
                            out read,
                            IntPtr.Zero
                        )) {
                            throw new Win32Exception(Marshal.GetLastWin32Error());
                        }
                        if (read == 0) {
                            break;
                        }
                        if (output.Length + read > maximumBytes) {
                            throw new InvalidDataException("secret env file is oversized");
                        }
                        output.Write(buffer, 0, (int)read);
                    }
                    bytes = output.ToArray();
                }
                ByHandleFileInformation after = GetIdentity(handle);
                string afterStability = Stability(handle, after);
                string afterDescriptor = GetDescriptor(handle);
                if (!String.Equals(beforeStability, afterStability, StringComparison.Ordinal) ||
                    !String.Equals(beforeDescriptor, afterDescriptor, StringComparison.Ordinal)) {
                    throw new InvalidDataException("secret file changed while its handle was held");
                }
                return new Snapshot {
                    Bytes = bytes,
                    SecurityDescriptor = afterDescriptor,
                    Stability = afterStability,
                    LinkCount = after.NumberOfLinks
                };
            }
        }

        public static string GetDirectoryDescriptor(string path) {
            using (SafeFileHandle handle = OpenNoFollow(path, true)) {
                ByHandleFileInformation identity = GetIdentity(handle);
                if ((identity.FileAttributes & FileAttributeReparsePoint) != 0 ||
                    (identity.FileAttributes & FileAttributeDirectory) == 0) {
                    throw new InvalidDataException("secret parent is a reparse point or non-directory");
                }
                return GetDescriptor(handle);
            }
        }
    }
}
'@
    }
    try {
        $snapshot = [AasSecretFile.NativeMethods]::ReadSnapshot($absolute, 65536)
        $descriptorChecks = [System.Collections.Generic.List[object]]::new()
        [void]$descriptorChecks.Add([pscustomobject]@{
            Descriptor = $snapshot.SecurityDescriptor
            Strict = $true
            Dacl = $true
        })
        $ancestor = [System.IO.Directory]::GetParent($absolute)
        $immediateParent = $true
        while ($null -ne $ancestor) {
            [void]$descriptorChecks.Add([pscustomobject]@{
                Descriptor = [AasSecretFile.NativeMethods]::GetDirectoryDescriptor(
                    $ancestor.FullName
                )
                Strict = $immediateParent
                # Predicate-check the DACL on the file and its immediate parent
                # only.  Every ancestor is still opened no-follow and still has
                # its owner checked.  Stock Windows grants `Authenticated Users`
                # add-subdirectory on the drive root, so demanding a
                # mutation-free DACL all the way up to `C:\` can never be
                # satisfied.  Nothing is given up: a right on an ancestor
                # confers no access to an existing descendant, and any grant
                # that does reach this file by inheritance is materialised into
                # its own DACL, which is checked strictly above.
                Dacl = $immediateParent
            })
            $immediateParent = $false
            $ancestor = $ancestor.Parent
        }
    } catch {
        throw "Secret env file or ancestor could not be read through guarded no-follow handles"
    }
    if ($snapshot.LinkCount -ne 1) {
        throw "Secret env file must have exactly one link"
    }

    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $currentSid = $identity.User.Value
    $allowedSids = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )
    [void]$allowedSids.Add($currentSid)
    [void]$allowedSids.Add("S-1-5-18")
    [void]$allowedSids.Add("S-1-5-32-544")
    $trustedOwnerSids = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )
    foreach ($sid in @(
        $currentSid,
        "S-1-5-18",
        "S-1-5-32-544",
        "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
    )) {
        [void]$trustedOwnerSids.Add($sid)
    }
    # Generic-rights ACEs (GENERIC_READ is bit 31) surface as negative Int32
    # access masks; a range-checked [uint32] conversion would throw and skip
    # that ACE's mutation check entirely.  Widening to Int64 keeps every ACE
    # checked; sign extension cannot reach the mask, whose high bits are zero.
    [int64]$mutationMask = 0x500D0156
    foreach ($descriptorCheck in $descriptorChecks) {
        $descriptorText = [string]$descriptorCheck.Descriptor
        try {
            $descriptor = [System.Security.AccessControl.RawSecurityDescriptor]::new(
                $descriptorText
            )
        } catch {
            throw "Secret env owner/DACL descriptor could not be parsed"
        }
        if (
            -not $descriptor.Owner -or
            (
                $descriptorCheck.Strict -and
                -not [System.String]::Equals(
                    $descriptor.Owner.Value,
                    $currentSid,
                    [System.StringComparison]::OrdinalIgnoreCase
                )
            ) -or
            (
                -not $descriptorCheck.Strict -and
                -not $trustedOwnerSids.Contains($descriptor.Owner.Value)
            )
        ) {
            throw "Secret env path has an untrusted owner in its ancestor chain"
        }
        if (-not $descriptorCheck.Dacl) {
            continue
        }
        if ($null -eq $descriptor.DiscretionaryAcl) {
            throw "Secret env file and immediate parent must have a non-null DACL"
        }
        foreach ($ace in $descriptor.DiscretionaryAcl) {
            if (
                $ace -isnot [System.Security.AccessControl.QualifiedAce] -or
                $ace.AceQualifier -ne [System.Security.AccessControl.AceQualifier]::AccessAllowed
            ) {
                continue
            }
            if (-not $ace.SecurityIdentifier) {
                throw "Secret env path contains an allow ACE without a principal"
            }
            # An INHERIT_ONLY ace confers nothing on the object carrying it; it
            # only seeds children, whose own DACLs are checked when they are
            # guarded in turn.
            if (
                ([int]$ace.AceFlags -band
                    [int][System.Security.AccessControl.AceFlags]::InheritOnly) -ne 0
            ) {
                continue
            }
            if (-not $allowedSids.Contains($ace.SecurityIdentifier.Value)) {
                if (
                    $descriptorCheck.Strict -or
                    (([int64]$ace.AccessMask -band $mutationMask) -ne 0)
                ) {
                    throw "Secret env path grants unsafe access outside the trusted ancestor boundary"
                }
            }
        }
    }
    if ($ValidateOnly.IsPresent) {
        if (-not $RetainPointer.IsPresent) {
            [Environment]::SetEnvironmentVariable(
                $PointerEnv,
                $null,
                [System.EnvironmentVariableTarget]::Process
            )
        }
        return
    }
    $bytes = $snapshot.Bytes
    try {
        $utf8 = [System.Text.UTF8Encoding]::new($false, $true)
        $text = $utf8.GetString($bytes)
    } catch {
        throw "Secret env file must be UTF-8"
    }

    $loaded = [System.Collections.Generic.Dictionary[string,string]]::new(
        [System.StringComparer]::Ordinal
    )
    if ($Format -eq "json") {
        $parsed = ConvertFrom-AasStrictFlatJson -Text $text
        foreach ($entry in $parsed.GetEnumerator()) {
            $key = [string]$entry.Key
            $value = [string]$entry.Value
            if ($key -cnotmatch '^[A-Z][A-Z0-9_]*$') {
                throw "Secret JSON contains an invalid key"
            }
            if (-not $allowed.Contains($key)) {
                throw "Secret JSON uses unsupported key $key"
            }
            if (-not $value -or $value -ne $value.Trim()) {
                throw "Secret JSON value for $key must be non-empty and unpadded"
            }
            if ($value.IndexOf([char]0) -ge 0) {
                throw "Secret JSON value for $key contains NUL"
            }
            $loaded[$key] = $value
        }
    } else {
        $seen = [System.Collections.Generic.HashSet[string]]::new(
            [System.StringComparer]::Ordinal
        )
        $lineNumber = 0
        foreach ($raw in [regex]::Split($text, "`r`n|`n|`r")) {
            $lineNumber += 1
            $line = $raw.Trim()
            if (-not $line -or $line.StartsWith("#", [System.StringComparison]::Ordinal)) {
                continue
            }
            if ($raw -ne $line) {
                throw "Secret env assignment at line $lineNumber has surrounding whitespace"
            }
            $separator = $raw.IndexOf("=", [System.StringComparison]::Ordinal)
            if ($separator -lt 1) {
                throw "Secret env assignment at line $lineNumber is missing '='"
            }
            $key = $raw.Substring(0, $separator)
            $value = $raw.Substring($separator + 1)
            if ($key -cnotmatch '^[A-Z][A-Z0-9_]*$') {
                throw "Secret env assignment at line $lineNumber has an invalid key"
            }
            if (-not $allowed.Contains($key)) {
                throw "Secret env assignment at line $lineNumber uses unsupported key $key"
            }
            if (-not $seen.Add($key)) {
                throw "Secret env assignment at line $lineNumber duplicates key $key"
            }
            if (-not $value -or $value -ne $value.Trim()) {
                throw "Secret env assignment at line $lineNumber has an empty or padded value for $key"
            }
            foreach ($character in $value.ToCharArray()) {
                if ([char]::IsControl($character)) {
                    throw "Secret env assignment at line $lineNumber has a control character for $key"
                }
            }
            $loaded[$key] = $value
        }
    }
    if ($subsetMode) {
        foreach ($key in $AllowedKeys) {
            [Environment]::SetEnvironmentVariable(
                $key,
                $null,
                [System.EnvironmentVariableTarget]::Process
            )
        }
        if (-not $RetainPointer.IsPresent) {
            [Environment]::SetEnvironmentVariable(
                $PointerEnv,
                $null,
                [System.EnvironmentVariableTarget]::Process
            )
        }
    }
    foreach ($entry in $loaded.GetEnumerator()) {
        if (-not $selected.Contains($entry.Key)) {
            continue
        }
        [Environment]::SetEnvironmentVariable(
            $entry.Key,
            $entry.Value,
            [System.EnvironmentVariableTarget]::Process
        )
    }
}
