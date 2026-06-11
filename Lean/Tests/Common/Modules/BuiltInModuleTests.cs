using System.Collections.Generic;
using System.IO;
using NodaTime;
using QuantConnect.Algorithm;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Algorithm.Framework.Selection;
using QuantConnect.Algorithm.Modules;
using QuantConnect.Brokerages;
using QuantConnect.Data;
using QuantConnect.Data.Auxiliary;
using QuantConnect.Data.Market;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Lean.Engine.DataFeeds;
using QuantConnect.Modules;
using NUnit.Framework;

namespace QuantConnect.Tests.Common.Modules
{
    [TestFixture]
    public sealed class BuiltInModuleTests
    {
        [Test]
        public void AlphaModelImplementsModuleContract()
        {
            var alpha = new TestAlphaModel();

            Assert.That(alpha, Is.InstanceOf<IModule>());
            Assert.That(alpha.Kind, Is.EqualTo(ModuleKind.Signal));
            Assert.That(alpha.ActivationMode, Is.EqualTo(ModuleActivationMode.BuiltIn));
            Assert.That(alpha.HotSwapMode, Is.EqualTo(ModuleHotSwapMode.Live));
        }

        [Test]
        public void UniverseSelectionModelImplementsModuleContract()
        {
            var universe = new TestUniverseSelectionModel();

            Assert.That(universe, Is.InstanceOf<IModule>());
            Assert.That(universe.Kind, Is.EqualTo(ModuleKind.Universe));
            Assert.That(universe.ActivationMode, Is.EqualTo(ModuleActivationMode.BuiltIn));
            Assert.That(universe.HotSwapMode, Is.EqualTo(ModuleHotSwapMode.Live));
        }

        [Test]
        public void RiskManagementModelImplementsModuleContract()
        {
            var risk = new TestRiskManagementModel();

            Assert.That(risk, Is.InstanceOf<IModule>());
            Assert.That(risk.Kind, Is.EqualTo(ModuleKind.Constraint));
            Assert.That(risk.ActivationMode, Is.EqualTo(ModuleActivationMode.BuiltIn));
            Assert.That(risk.HotSwapMode, Is.EqualTo(ModuleHotSwapMode.Live));
        }

        [Test]
        public void DefaultDataProviderImplementsModuleContract()
        {
            using var provider = new DefaultDataProvider();

            Assert.That(provider, Is.InstanceOf<IModule>());
            Assert.That(provider.Kind, Is.EqualTo(ModuleKind.DataSource));
            Assert.That(provider.ActivationMode, Is.EqualTo(ModuleActivationMode.BuiltIn));
            Assert.That(provider.HotSwapMode, Is.EqualTo(ModuleHotSwapMode.Live));
        }

        [Test]
        public void HistoryProviderBaseImplementsModuleContract()
        {
            var history = new TestHistoryProvider();

            Assert.That(history, Is.InstanceOf<IModule>());
            Assert.That(history.Kind, Is.EqualTo(ModuleKind.DataSource));
            Assert.That(history.ActivationMode, Is.EqualTo(ModuleActivationMode.BuiltIn));
            Assert.That(history.HotSwapMode, Is.EqualTo(ModuleHotSwapMode.Live));
        }

        [Test]
        public void ExecutionModelImplementsModuleContract()
        {
            var execution = new TestExecutionModel();

            Assert.That(execution, Is.InstanceOf<IModule>());
            Assert.That(execution.Kind, Is.EqualTo(ModuleKind.Execution));
            Assert.That(execution.ActivationMode, Is.EqualTo(ModuleActivationMode.BuiltIn));
            Assert.That(execution.HotSwapMode, Is.EqualTo(ModuleHotSwapMode.RequiresPause));
        }

        [Test]
        public void AuxiliaryDataProvidersImplementModuleContract()
        {
            Assert.That(new LocalDiskMapFileProvider(), Is.InstanceOf<IModule>());
            Assert.That(new LocalDiskFactorFileProvider(), Is.InstanceOf<IModule>());
            Assert.That(new LocalZipMapFileProvider(), Is.InstanceOf<IModule>());
            Assert.That(new LocalZipFactorFileProvider(), Is.InstanceOf<IModule>());
        }

        [Test]
        public void DefaultBrokerageModelImplementsModuleContract()
        {
            var brokerage = new DefaultBrokerageModel();

            Assert.That(brokerage, Is.InstanceOf<IModule>());
            Assert.That(brokerage.Kind, Is.EqualTo(ModuleKind.MarketRule));
            Assert.That(brokerage.ActivationMode, Is.EqualTo(ModuleActivationMode.BuiltIn));
            Assert.That(brokerage.HotSwapMode, Is.EqualTo(ModuleHotSwapMode.RequiresFlatNoOrders));
        }

        [Test]
        public void DefaultMarketRuleModuleCanBeCreatedByReflectionFactory()
        {
            var configuration = new ModuleConfiguration(
                "market.default",
                ModuleKind.MarketRule,
                ModuleActivationMode.BuiltIn,
                typeof(DefaultMarketRuleModule).FullName,
                "builtin",
                ModuleHotSwapMode.RequiresFlatNoOrders,
                new Dictionary<string, string>());

            var module = new ReflectionModuleFactory().CreateAsync(configuration).AsTask().Result;

            Assert.That(module, Is.InstanceOf<DefaultMarketRuleModule>());
            Assert.That(module.Key, Is.EqualTo("market.default"));
            Assert.That(module.Kind, Is.EqualTo(ModuleKind.MarketRule));
        }

        [Test]
        public void ReflectionFactoryBindsConfigToOptionalConstructorParameters()
        {
            var configuration = new ModuleConfiguration(
                "signal.ema",
                ModuleKind.Signal,
                ModuleActivationMode.BuiltIn,
                typeof(EmaCrossAlphaModel).FullName,
                "builtin",
                ModuleHotSwapMode.Live,
                new Dictionary<string, string>
                {
                    ["config"] = "{\"fastPeriod\":8,\"slowPeriod\":21,\"resolution\":\"Daily\"}"
                });

            var module = new ReflectionModuleFactory().CreateAsync(configuration).AsTask().Result;

            Assert.That(module, Is.InstanceOf<EmaCrossAlphaModel>());
            Assert.That(module.Key, Is.EqualTo("signal.ema"));
            Assert.That(((EmaCrossAlphaModel)module).Name, Is.EqualTo("EmaCrossAlphaModel(8,21,Daily)"));
        }

        [Test]
        public void PipelineManifestJsonLoaderParsesAlphaGraph()
        {
            var path = Path.Combine(TestContext.CurrentContext.WorkDirectory, "alpha-graph-pipeline.json");
            File.WriteAllText(path, $$"""
            {
              "name": "alpha-graph-test",
              "modules": [
                {
                  "key": "alpha.source",
                  "kind": "Signal",
                  "activationMode": "BuiltIn",
                  "entryPoint": "{{typeof(NullAlphaModel).FullName}}",
                  "version": "builtin",
                  "hotSwapMode": "Live",
                  "parameters": {}
                },
                {
                  "key": "alpha.model",
                  "kind": "Signal",
                  "activationMode": "BuiltIn",
                  "entryPoint": "{{typeof(NullAlphaModel).FullName}}",
                  "version": "builtin",
                  "hotSwapMode": "Live",
                  "parameters": {}
                }
              ],
              "signal": [],
              "alphaGraph": {
                "nodes": ["alpha.source", "alpha.model"],
                "outputs": {
                  "insights": ["insight_1"]
                },
                "bindings": {
                  "alpha.source": {
                    "instanceId": "alpha.source",
                    "moduleId": "price-source",
                    "version": "20260524-001",
                    "config": {},
                    "inputs": {},
                    "outputs": {
                      "price": "price_1"
                    }
                  },
                  "alpha.model": {
                    "instanceId": "alpha.model",
                    "moduleId": "direction-to-price-insight",
                    "version": "20260524-001",
                    "config": {
                      "period": 20
                    },
                    "inputs": {
                      "price": "price_1"
                    },
                    "outputs": {
                      "insight": "insight_1"
                    }
                  }
                },
                "edges": [
                  {
                    "wire": "price_1",
                    "from": {
                      "node": "alpha.source",
                      "port": "price",
                      "type": "series.price"
                    },
                    "to": {
                      "node": "alpha.model",
                      "port": "price",
                      "type": "series.price"
                    }
                  }
                ]
              }
            }
            """);

            var manifest = PipelineManifestJsonLoader.Load(path);

            Assert.That(manifest.SignalModules, Is.Empty);
            Assert.That(manifest.AlphaGraph.HasNodes, Is.True);
            Assert.That(manifest.AlphaGraph.Nodes, Is.EqualTo(new[] { "alpha.source", "alpha.model" }));
            Assert.That(manifest.AlphaGraph.Outputs["insights"], Is.EqualTo(new[] { "insight_1" }));
            Assert.That(manifest.AlphaGraph.Bindings["alpha.model"].Inputs["price"], Is.EqualTo("price_1"));
            Assert.That(manifest.AlphaGraph.Bindings["alpha.model"].Config["period"], Is.EqualTo("20"));
            Assert.That(manifest.AlphaGraph.Edges, Has.Count.EqualTo(1));
        }

        private sealed class TestAlphaModel : AlphaModel
        {
            public override IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
            {
                return new List<Insight>();
            }
        }

        private sealed class TestUniverseSelectionModel : UniverseSelectionModel
        {
            public override IEnumerable<Universe> CreateUniverses(QCAlgorithm algorithm)
            {
                return new List<Universe>();
            }
        }

        private sealed class TestRiskManagementModel : RiskManagementModel
        {
            public override IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm algorithm, IPortfolioTarget[] targets)
            {
                return targets;
            }
        }

        private sealed class TestExecutionModel : ExecutionModel
        {
            public override void Execute(QCAlgorithm algorithm, IPortfolioTarget[] targets)
            {
            }
        }

        private sealed class TestHistoryProvider : HistoryProviderBase
        {
            public override int DataPointCount => 0;

            public override void Initialize(HistoryProviderInitializeParameters parameters)
            {
            }

            public override IEnumerable<Slice> GetHistory(IEnumerable<HistoryRequest> requests, DateTimeZone sliceTimeZone)
            {
                yield break;
            }
        }
    }
}
