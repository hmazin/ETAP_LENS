using System;
using System.Threading;
using System.Threading.Tasks;

namespace EtapCrystalReporter.Utilities
{
    public static class StaTask
    {
        public static Task<T> Run<T>(Func<T> work)
        {
            var completion = new TaskCompletionSource<T>();
            var thread = new Thread(delegate()
            {
                try { completion.SetResult(work()); }
                catch (Exception ex) { completion.SetException(ex); }
            });
            thread.IsBackground = true;
            thread.SetApartmentState(ApartmentState.STA);
            thread.Start();
            return completion.Task;
        }
    }
}
