using System;
using System.Drawing;
using System.Windows.Forms;
using EtapCrystalReporter.Utilities;

namespace EtapCrystalReporter.UI
{
    public sealed class SettingsForm : Form
    {
        public SettingsForm(AppSettings settings)
        {
            Ui.Style(this);
            Text = "Template settings";
            ClientSize = new Size(650, 190);
            MinimumSize = new Size(580, 230);
            var panel = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(16), ColumnCount = 2, RowCount = 4 };
            panel.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            panel.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            var directory = new TextBox { Text = settings.TemplateDirectory, Dock = DockStyle.Fill };
            panel.Controls.Add(Ui.Label("Folder containing Crystal .rpt templates (including subfolders)"), 0, 0);
            panel.SetColumnSpan(panel.GetControlFromPosition(0, 0), 2);
            panel.Controls.Add(directory, 0, 1);
            panel.Controls.Add(Ui.Button("Browse…", delegate { directory.Text = Ui.Folder(this, directory.Text); }), 1, 1);
            var note = Ui.Label("Add your licensed ETAP templates here, then reload the template list.");
            panel.Controls.Add(note, 0, 2); panel.SetColumnSpan(note, 2);
            var save = Ui.Button("Save", delegate
            {
                if (string.IsNullOrWhiteSpace(directory.Text)) { Ui.Error(this, new ArgumentException("Select a template folder.")); return; }
                try { settings.TemplateDirectory = System.IO.Path.GetFullPath(directory.Text); settings.Save(); DialogResult = DialogResult.OK; }
                catch (Exception ex) { Ui.Error(this, ex); }
            });
            panel.Controls.Add(save, 1, 3);
            AcceptButton = save;
            Controls.Add(panel);
        }
    }
}
