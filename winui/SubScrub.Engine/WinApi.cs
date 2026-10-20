using System.Runtime.InteropServices;
using System.Text;

namespace SubScrub.Engine;

/// <summary>The Windows parts of the engine: the Recycle Bin, the "files in use" check (Restart Manager), network
/// drives and free space. Elsewhere (Linux, for the checks in CI) they fall back to the freedesktop trash, "nothing
/// open", "not a network drive" and the drive's free space.</summary>
public static class WinApi
{
    // ================================================================ the Recycle Bin

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct SHFILEOPSTRUCT
    {
        public IntPtr hwnd;
        public uint wFunc;
        public string pFrom;
        public string? pTo;
        public ushort fFlags;
        public int fAnyOperationsAborted;
        public IntPtr hNameMappings;
        public string? lpszProgressTitle;
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    private static extern int SHFileOperationW(ref SHFILEOPSTRUCT op);

    private const uint FO_DELETE = 3;
    private const ushort FOF_SILENT = 0x4, FOF_NOCONFIRMATION = 0x10, FOF_ALLOWUNDO = 0x40, FOF_NOCONFIRMMKDIR = 0x200,
                         FOF_NOERRORUI = 0x400;

    /// <summary>send2trash: moves the file to the Recycle Bin (undo-able), or throws RecycleError.</summary>
    public static void SendToTrash(string path)
    {
        var full = Path.GetFullPath(path);
        if (!Path.Exists(full))
            throw new FileNotFoundException($"File not found: {full}", full);
        if (!OperatingSystem.IsWindows())
        {
            FreedesktopTrash(full);
            return;
        }
        var op = new SHFILEOPSTRUCT
        {
            wFunc = FO_DELETE, pFrom = full + "\0\0",
            fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT | FOF_NOCONFIRMMKDIR,
        };
        var r = SHFileOperationW(ref op);
        if (r != 0)
            throw new RecycleError($"Couldn't perform operation. Error code: {r}", r);
        if (op.fAnyOperationsAborted != 0)
            throw new RecycleError("The move to the Recycle Bin was cancelled");
    }

    private static void FreedesktopTrash(string full)
    {
        var data = Environment.GetEnvironmentVariable("XDG_DATA_HOME") is { Length: > 0 } x ? x
            : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".local", "share");
        var files = Path.Combine(data, "Trash", "files");
        var info = Path.Combine(data, "Trash", "info");
        Directory.CreateDirectory(files);
        Directory.CreateDirectory(info);
        var name = Path.GetFileName(full);
        var dest = name;
        for (var i = 1; Path.Exists(Path.Combine(files, dest)) || File.Exists(Path.Combine(info, dest + ".trashinfo")); i++)
            dest = $"{Path.GetFileNameWithoutExtension(name)} {i}{Path.GetExtension(name)}";
        File.WriteAllText(Path.Combine(info, dest + ".trashinfo"),
                          $"[Trash Info]\nPath={Uri.EscapeDataString(full).Replace("%2F", "/")}\nDeletionDate={DateTime.Now:yyyy-MM-ddTHH:mm:ss}\n");
        if (Directory.Exists(full))
            Directory.Move(full, Path.Combine(files, dest));
        else
            File.Move(full, Path.Combine(files, dest));
    }

    /// <summary>The error means another program has the file open (sharing or lock violation).</summary>
    public static bool IsSharingViolation(Exception e) => (e.HResult & 0xFFFF) is 32 or 33;

    // ================================================================ network drives and free space

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    private static extern uint GetDriveTypeW(string root);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetDiskFreeSpaceExW(string dir, out ulong freeAvailable, out ulong total, out ulong totalFree);

    public static bool IsNetworkPath(string path)
    {
        if (!OperatingSystem.IsWindows())
            return false;
        var p = Path.GetFullPath(path);
        if (p.StartsWith(@"\\", StringComparison.Ordinal))
            return true;
        try
        {
            var root = Path.GetPathRoot(p);
            return !string.IsNullOrEmpty(root) && GetDriveTypeW(root.TrimEnd('\\') + "\\") == 4;   // DRIVE_REMOTE
        }
        catch (Exception)
        {
            return false;
        }
    }

    public static long FreeSpace(string folder)
    {
        if (OperatingSystem.IsWindows() && GetDiskFreeSpaceExW(folder, out var free, out _, out _))
            return (long)Math.Min(free, long.MaxValue);
        return long.MaxValue;
    }

    // ================================================================ files in use (Restart Manager)

    [StructLayout(LayoutKind.Sequential)]
    private struct RM_UNIQUE_PROCESS
    {
        public uint dwProcessId;
        public System.Runtime.InteropServices.ComTypes.FILETIME ProcessStartTime;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct RM_PROCESS_INFO
    {
        public RM_UNIQUE_PROCESS Process;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 256)] public string strAppName;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 64)] public string strServiceShortName;
        public int ApplicationType;
        public uint AppStatus;
        public uint TSSessionId;
        [MarshalAs(UnmanagedType.Bool)] public bool bRestartable;
    }

    [DllImport("rstrtmgr.dll", CharSet = CharSet.Unicode)]
    private static extern int RmStartSession(out uint pSessionHandle, int dwSessionFlags, StringBuilder strSessionKey);

    [DllImport("rstrtmgr.dll", CharSet = CharSet.Unicode)]
    private static extern int RmRegisterResources(uint pSessionHandle, uint nFiles, string[] rgsFilenames, uint nApplications,
                                                  [In] RM_UNIQUE_PROCESS[]? rgApplications, uint nServices, string[]? rgsServiceNames);

    [DllImport("rstrtmgr.dll")]
    private static extern int RmGetList(uint dwSessionHandle, out uint pnProcInfoNeeded, ref uint pnProcInfo,
                                        [In, Out] RM_PROCESS_INFO[] rgAffectedApps, ref uint lpdwRebootReasons);

    [DllImport("rstrtmgr.dll")]
    private static extern int RmEndSession(uint pSessionHandle);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct PROCESSENTRY32W
    {
        public uint dwSize;
        public uint cntUsage;
        public uint th32ProcessID;
        public IntPtr th32DefaultHeapID;
        public uint th32ModuleID;
        public uint cntThreads;
        public uint th32ParentProcessID;
        public int pcPriClassBase;
        public uint dwFlags;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] public string szExeFile;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr CreateToolhelp32Snapshot(uint flags, uint pid);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool Process32FirstW(IntPtr snap, ref PROCESSENTRY32W e);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool Process32NextW(IntPtr snap, ref PROCESSENTRY32W e);

    [DllImport("kernel32.dll")]
    private static extern bool CloseHandle(IntPtr h);

    /// <summary>PIDs of the tools SubScrub itself is running right now (ffprobe, mkvextract…).</summary>
    private static HashSet<uint> OwnChildPids()
    {
        var kids = new HashSet<uint>();
        if (!OperatingSystem.IsWindows())
            return kids;
        try
        {
            var snap = CreateToolhelp32Snapshot(0x2, 0);              // TH32CS_SNAPPROCESS
            if (snap == IntPtr.Zero || snap == new IntPtr(-1))
                return kids;
            try
            {
                var me = (uint)Environment.ProcessId;
                var e = new PROCESSENTRY32W { dwSize = (uint)Marshal.SizeOf<PROCESSENTRY32W>() };
                var ok = Process32FirstW(snap, ref e);
                while (ok)
                {
                    if (e.th32ParentProcessID == me)
                        kids.Add(e.th32ProcessID);
                    ok = Process32NextW(snap, ref e);
                }
            }
            finally
            {
                CloseHandle(snap);
            }
        }
        catch (Exception)
        {
        }
        return kids;
    }

    /// <summary>Which other programs have each file open, e.g. {path: ["qBittorrent"]}. Uses the Windows Restart
    /// Manager (what installers use to say "close these apps first"). Empty when nothing has them open, on other
    /// systems, or if Windows can't tell.</summary>
    public static Dictionary<string, List<string>> WhoHasOpen(List<string> paths)
    {
        var found = new Dictionary<string, List<string>>();
        var list = paths.Distinct().Where(p => !string.IsNullOrEmpty(p) && File.Exists(p)).ToList();
        if (!OperatingSystem.IsWindows() || list.Count == 0)
            return found;
        var ours = OwnChildPids();
        ours.Add((uint)Environment.ProcessId);
        foreach (var path in list)
        {
            uint session;
            try
            {
                if (RmStartSession(out session, 0, new StringBuilder(64)) != 0)
                    continue;
            }
            catch (Exception)
            {
                return found;
            }
            try
            {
                if (RmRegisterResources(session, 1, [Path.GetFullPath(path)], 0, null, 0, null) != 0)
                    continue;
                const int size = 16;
                var infos = new RM_PROCESS_INFO[size];
                uint count = size, reasons = 0;
                var r = RmGetList(session, out _, ref count, infos, ref reasons);
                if (r == 234)                                          // ERROR_MORE_DATA
                {
                    found[path] = ["several programs"];
                    continue;
                }
                if (r != 0)
                    continue;
                var names = new List<string>();
                for (var i = 0; i < Math.Min(count, size); i++)
                {
                    var pi = infos[i];
                    if (ours.Contains(pi.Process.dwProcessId))
                        continue;
                    var n = Py.Strip(pi.strAppName ?? "");
                    names.Add(n.Length > 0 ? n : $"program #{pi.Process.dwProcessId}");
                }
                if (names.Count > 0)
                    found[path] = names.Distinct().Order(PyFmt.Order).ToList();
            }
            catch (Exception)
            {
            }
            finally
            {
                try
                {
                    RmEndSession(session);
                }
                catch (Exception)
                {
                }
            }
        }
        return found;
    }
}
