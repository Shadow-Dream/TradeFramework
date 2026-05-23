using System;
using System.Collections.Generic;
using System.Threading;
using QuantConnect.Data;
using QuantConnect.Interfaces;

namespace QuantConnect.Algorithm.CSharp
{
    public class PipelineHotSwapShellAlgorithm : QCAlgorithm, IRegressionAlgorithmDefinition
    {
        public override void Initialize()
        {
            SetStartDate(2014, 6, 5);
            SetEndDate(2014, 6, 20);
            SetCash(100000);
        }

        public override void OnData(Slice slice)
        {
            SetRuntimeStatistic("HotSwapDate", Time.ToString("yyyy-MM-dd"));
            QuantConnect.Logging.Log.Trace($"SHELL:{Time:yyyy-MM-dd}:SECURITIES={string.Join(",", Securities.Keys)}");
            Thread.Sleep(600);
        }

        public bool CanRunLocally => true;
        public List<Language> Languages => new() { Language.CSharp };
        public long DataPoints => 0;
        public int AlgorithmHistoryDataPoints => 0;
        public AlgorithmStatus AlgorithmStatus => AlgorithmStatus.Completed;
        public Dictionary<string, string> ExpectedStatistics => new();
    }
}
