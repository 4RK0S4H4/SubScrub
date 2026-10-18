using System.Diagnostics;
using System.Runtime.InteropServices;

namespace SubScrub.Services;

/// <summary>A Windows job object that ends the engine (and the tools it runs) when SubScrub ends, even if
/// SubScrub is killed rather than closed.</summary>
internal static unsafe partial class Job
{
    [StructLayout(LayoutKind.Sequential)]
    private struct BasicLimits
    {
        public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
        public uint LimitFlags;
        public nuint MinimumWorkingSetSize, MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public nuint Affinity;
        public uint PriorityClass, SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ExtendedLimits
    {
        public BasicLimits Basic;
        public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes;
        public nuint ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
    }

    [LibraryImport("kernel32.dll", EntryPoint = "CreateJobObjectW")]
    private static partial nint CreateJobObject(nint attributes, nint name);

    [LibraryImport("kernel32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool SetInformationJobObject(nint job, int infoClass, void* info, uint size);

    [LibraryImport("kernel32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool AssignProcessToJobObject(nint job, nint process);

    private static nint _job;

    public static void Attach(Process process)
    {
        try
        {
            if (_job == 0)
            {
                var job = CreateJobObject(0, 0);
                if (job == 0)
                    return;
                var limits = new ExtendedLimits();
                limits.Basic.LimitFlags = 0x2000;                    // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                SetInformationJobObject(job, 9, &limits, (uint)sizeof(ExtendedLimits));
                _job = job;
            }
            AssignProcessToJobObject(_job, process.Handle);
        }
        catch (Exception)
        {
            // not fatal: the engine also stops when its input closes
        }
    }
}
