using Microsoft.UI.Xaml;
using SubScrub.Services;

namespace SubScrub;

public partial class App : Application
{
    private MainWindow? _window;

    public App()
    {
        InitializeComponent();
        UnhandledException += (_, e) => Shots.Crash(e.Exception);
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        if (Shots.Dir != null)
        {
            Directory.CreateDirectory(Shots.Dir);
            try
            {
                Diagnose.Run(Shots.Dir);
            }
            catch (Exception e)
            {
                File.AppendAllText(Path.Combine(Shots.Dir, "diagnose.txt"), "diagnose failed: " + e + "\n");
            }
        }
        _window = new MainWindow();
        _window.Activate();
    }
}
