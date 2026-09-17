using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Windows.Forms;

namespace EtapCrystalReporter.Services
{
    // Late binding keeps SAP's licensed assemblies out of source control and allows
    // database inspection without SAP installed. Reporting always uses the real SDK.
    public sealed class CrystalRuntime
    {
        private Assembly engine;
        private Assembly shared;
        public string Version { get { EnsureLoaded(); return engine.FullName; } }

        public void EnsureLoaded()
        {
            if (engine != null && shared != null) return;
            try
            {
                engine = Load("CrystalDecisions.CrystalReports.Engine");
                shared = Load("CrystalDecisions.Shared");
                if (engine.GetName().Version != shared.GetName().Version)
                    throw new InvalidOperationException("Crystal Reports assembly versions do not match.");
            }
            catch (Exception ex)
            {
                engine = null; shared = null;
                throw new InvalidOperationException("SAP Crystal Reports for Visual Studio runtime is unavailable or incompatible. Install the " +
                    (Environment.Is64BitProcess ? "64-bit" : "32-bit") + " runtime matching this application. " + ex.Message, ex);
            }
        }

        private static Assembly Load(string name)
        {
            try { return Assembly.Load(name); }
            catch (FileNotFoundException) { }
            foreach (string version in new[] { "13.0.4000.0", "13.0.3500.0", "13.0.2000.0", "13.0.0.0" })
            {
                try { return Assembly.Load(name + ", Version=" + version + ", Culture=neutral, PublicKeyToken=692fbea5521e1304"); }
                catch (FileNotFoundException) { }
            }
            string gac = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "Microsoft.NET", "assembly", "GAC_MSIL", name);
            if (Directory.Exists(gac))
            {
                string candidate = Directory.EnumerateFiles(gac, name + ".dll", SearchOption.AllDirectories)
                    .Where(x => AssemblyName.GetAssemblyName(x).Version.Major == 13).OrderByDescending(x => AssemblyName.GetAssemblyName(x).Version).FirstOrDefault();
                if (candidate != null) return Assembly.LoadFrom(candidate);
            }
            throw new FileNotFoundException(name + " was not found.");
        }

        public dynamic NewDocument()
        {
            EnsureLoaded();
            return Activator.CreateInstance(engine.GetType("CrystalDecisions.CrystalReports.Engine.ReportDocument", true));
        }

        public void CheckExecution()
        {
            dynamic probe = NewDocument();
            try { probe.Close(); }
            finally { probe.Dispose(); }
        }

        public dynamic EnumValue(string type, string member)
        { EnsureLoaded(); return Enum.Parse(shared.GetType("CrystalDecisions.Shared." + type, true), member); }

        public Control CreateViewer()
        {
            EnsureLoaded();
            Assembly forms = Load("CrystalDecisions.Windows.Forms");
            if (forms.GetName().Version != engine.GetName().Version)
                throw new InvalidOperationException("Crystal viewer and engine versions do not match. Repair the SAP runtime installation.");
            return (Control)Activator.CreateInstance(forms.GetType("CrystalDecisions.Windows.Forms.CrystalReportViewer", true));
        }
    }
}
