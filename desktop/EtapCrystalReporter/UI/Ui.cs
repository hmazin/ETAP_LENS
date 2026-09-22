using System;
using System.Diagnostics;
using System.Drawing;
using System.Windows.Forms;

namespace EtapCrystalReporter.UI
{
    internal static class Ui
    {
        internal const string StudyFilter = "ETAP study results (*.SA1S;*.SA2S;*.UL1S)|*.SA1S;*.SA2S;*.UL1S";
        internal static Button Button(string text, EventHandler click)
        {
            var button = new Button { Text = text, AutoSize = true, MinimumSize = new Size(105, 32), Margin = new Padding(4) };
            button.Click += click;
            return button;
        }
        internal static Label Label(string text)
        { return new Label { Text = text, AutoSize = true, Anchor = AnchorStyles.Left, Margin = new Padding(4, 8, 4, 8) }; }
        internal static string Folder(IWin32Window owner, string current)
        {
            using (var dialog = new FolderBrowserDialog { SelectedPath = current, Description = "Select a folder" })
                return dialog.ShowDialog(owner) == DialogResult.OK ? dialog.SelectedPath : current;
        }
        internal static void Open(string path) { Process.Start(new ProcessStartInfo(path) { UseShellExecute = true }); }
        internal static void Error(IWin32Window owner, Exception error)
        { MessageBox.Show(owner, error.Message, "ETAP Crystal Report Generator", MessageBoxButtons.OK, MessageBoxIcon.Error); }
        internal static void Style(Form form)
        {
            form.Font = new Font("Segoe UI", 9F);
            form.AutoScaleMode = AutoScaleMode.Dpi;
            form.StartPosition = FormStartPosition.CenterParent;
        }
    }
}
