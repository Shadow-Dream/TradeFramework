using System.Collections.Generic;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Algorithm.Framework.Selection;
using QuantConnect.Brokerages;
using QuantConnect.Configuration;
using QuantConnect.Modules;
using NUnit.Framework;

namespace QuantConnect.Tests.Common.Modules
{
    [TestFixture]
    public sealed class PipelineBindingTests
    {
        private string _manifestPath;

        [SetUp]
        public void SetUp()
        {
            _manifestPath = Path.Combine(Path.GetTempPath(), $"pipeline-{Path.GetRandomFileName()}.json");
            Config.Reset();
        }

        [TearDown]
        public void TearDown()
        {
            if (File.Exists(_manifestPath))
            {
                File.Delete(_manifestPath);
            }
            Config.Reset();
        }

        [Test]
        public void QcAlgorithmBindsPipelineManifestFromConfig()
        {
            File.WriteAllText(_manifestPath, JsonConvert.SerializeObject(new
            {
                name = "qc-binding",
                modules = new object[]
                {
                    new { key = "universe.main", kind = "Universe", activationMode = "BuiltIn", entryPoint = typeof(TestUniverseModule).FullName, hotSwapMode = "Live" },
                    new { key = "signal.main", kind = "Signal", activationMode = "BuiltIn", entryPoint = typeof(TestAlphaModule).FullName, hotSwapMode = "Live" },
                    new { key = "target.main", kind = "Target", activationMode = "BuiltIn", entryPoint = typeof(TestTargetModule).FullName, hotSwapMode = "Live" },
                    new { key = "constraint.main", kind = "Constraint", activationMode = "BuiltIn", entryPoint = typeof(TestRiskModule).FullName, hotSwapMode = "Live" },
                    new { key = "execution.main", kind = "Execution", activationMode = "BuiltIn", entryPoint = typeof(TestExecutionModule).FullName, hotSwapMode = "RequiresPause" },
                    new { key = "market.main", kind = "MarketRule", activationMode = "BuiltIn", entryPoint = typeof(TestBrokerageModule).FullName, hotSwapMode = "RequiresFlatNoOrders" }
                },
                universe = new[] { "universe.main" },
                signal = new[] { "signal.main" },
                target = new[] { "target.main" },
                constraint = new[] { "constraint.main" },
                execution = new[] { "execution.main" },
                marketRule = "market.main"
            }));

            Config.Set("pipeline-manifest", _manifestPath);
            Config.Set("data-folder", "/file/share/data_jyz/trade/Lean/Data/");
            QuantConnect.Globals.Reset();

            var algorithm = new QCAlgorithm();
            algorithm.FrameworkPostInitialize();

            Assert.That(algorithm.UniverseSelection, Is.TypeOf<TestUniverseModule>());
            Assert.That(algorithm.Alpha, Is.TypeOf<TestAlphaModule>());
            Assert.That(algorithm.PortfolioConstruction, Is.TypeOf<TestTargetModule>());
            Assert.That(algorithm.RiskManagement, Is.TypeOf<TestRiskModule>());
            Assert.That(algorithm.Execution, Is.TypeOf<TestExecutionModule>());
            Assert.That(algorithm.BrokerageModel, Is.TypeOf<TestBrokerageModule>());
        }

        private sealed class TestUniverseModule : UniverseSelectionModel
        {
            public override IEnumerable<QuantConnect.Data.UniverseSelection.Universe> CreateUniverses(QCAlgorithm algorithm)
            {
                return [];
            }
        }

        private sealed class TestAlphaModule : AlphaModel
        {
            public override IEnumerable<Insight> Update(QCAlgorithm algorithm, QuantConnect.Data.Slice data)
            {
                return [];
            }
        }

        private sealed class TestTargetModule : PortfolioConstructionModel
        {
            public override IEnumerable<IPortfolioTarget> CreateTargets(QCAlgorithm algorithm, Insight[] insights)
            {
                return [];
            }
        }

        private sealed class TestRiskModule : RiskManagementModel
        {
            public override IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm algorithm, IPortfolioTarget[] targets)
            {
                return targets;
            }
        }

        private sealed class TestExecutionModule : ExecutionModel
        {
            public override void Execute(QCAlgorithm algorithm, IPortfolioTarget[] targets)
            {
            }
        }

        private sealed class TestBrokerageModule : DefaultBrokerageModel
        {
        }
    }
}
