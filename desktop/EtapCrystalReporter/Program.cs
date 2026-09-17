using System;
using System.Windows.Forms;
using EtapCrystalReporter.UI;
using EtapCrystalReporter.Utilities;

namespace EtapCrystalReporter
{
    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            try
            {
                var log = new Logger(System.IO.Path.Combine(AppSettings.DataDirectory, "Logs"));
                log.Write("application.started", new { version = "0.1.0", bitness = IntPtr.Size * 8 });
                AppSettings settings;
                try { settings = AppSettings.Load(); }
                catch (Exception ex)
                {
                    log.Write("settings.invalid", new { error = ex.Message });
                    MessageBox.Show("Settings could not be loaded; defaults will be used. " + ex.Message, "ETAP Crystal Report Generator");
                    settings = new AppSettings();
                }
                Application.Run(new MainForm(settings, log));
            }
            catch (Exception ex) { MessageBox.Show(ex.Message, "ETAP Crystal Report Generator", MessageBoxButtons.OK, MessageBoxIcon.Error); }
        }
    }
}
