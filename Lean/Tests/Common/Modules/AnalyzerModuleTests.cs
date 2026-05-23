using System.Collections.Generic;
using QuantConnect.Modules;
using NUnit.Framework;

namespace QuantConnect.Tests.Common.Modules
{
    [TestFixture]
    public sealed class AnalyzerModuleTests
    {
        [Test]
        public void AnalyzerResultCopiesValues()
        {
            var values = new Dictionary<string, object> { ["sharpe"] = 1.23m };
            var result = new AnalyzerResult(values);

            values["sharpe"] = 0m;

            Assert.That(result.Values["sharpe"], Is.EqualTo(1.23m));
        }

        [Test]
        public void AnalyzerModuleCanDeclareObservationDependencies()
        {
            var analyzer = new TestAnalyzerModule();

            Assert.That(analyzer.Kind, Is.EqualTo(ModuleKind.Analyzer));
            Assert.That(analyzer.RequiredObservations.Count, Is.EqualTo(2));
            Assert.That(analyzer.RequiredObservations, Does.Contain(ObservationKey.Create("equity", "curve")));
            Assert.That(analyzer.RequiredObservations, Does.Contain(ObservationKey.Create("reference", "manual_position", "ndx")));
        }

        [Test]
        public void AnalyzerModuleReadsOnlyRequestedInputs()
        {
            var analyzer = new TestAnalyzerModule();
            var observations = new Dictionary<ObservationKey, object>
            {
                [ObservationKey.Create("equity", "curve")] = "curve-data",
                [ObservationKey.Create("reference", "manual_position", "ndx")] = "manual-data",
                [ObservationKey.Create("unused", "field")] = "ignored"
            };

            var result = analyzer.Analyze(observations);

            Assert.That(result.Values["consumed"], Is.EqualTo(2));
            Assert.That(result.Values["manual"], Is.EqualTo("manual-data"));
        }

        private sealed class TestAnalyzerModule : IAnalyzerModule
        {
            private readonly ModuleState _state = new(typeof(TestAnalyzerModule), ModuleKind.Analyzer, ModuleHotSwapMode.RequiresPause);

            public string Key => _state.Key;
            public ModuleKind Kind => _state.Kind;
            public ModuleActivationMode ActivationMode => _state.ActivationMode;
            public string Version => _state.Version;
            public ModuleHotSwapMode HotSwapMode => _state.HotSwapMode;

            public IReadOnlyCollection<ObservationKey> RequiredObservations { get; } =
                new[]
                {
                    ObservationKey.Create("equity", "curve"),
                    ObservationKey.Create("reference", "manual_position", "ndx")
                };

            public AnalyzerResult Analyze(IReadOnlyDictionary<ObservationKey, object> observations)
            {
                var count = 0;
                foreach (var key in RequiredObservations)
                {
                    if (observations.ContainsKey(key))
                    {
                        count++;
                    }
                }

                return new AnalyzerResult(new Dictionary<string, object>
                {
                    ["consumed"] = count,
                    ["manual"] = observations[ObservationKey.Create("reference", "manual_position", "ndx")]
                });
            }

            public System.Threading.Tasks.ValueTask Initialize(ModuleConfiguration configuration, System.Threading.CancellationToken cancellationToken = default)
            {
                return _state.Initialize(configuration, cancellationToken);
            }

            public System.Threading.Tasks.ValueTask Pause(System.Threading.CancellationToken cancellationToken = default)
            {
                return _state.Pause(cancellationToken);
            }

            public System.Threading.Tasks.ValueTask Resume(System.Threading.CancellationToken cancellationToken = default)
            {
                return _state.Resume(cancellationToken);
            }

            public System.Threading.Tasks.ValueTask<ModuleSnapshot> CreateSnapshot(System.Threading.CancellationToken cancellationToken = default)
            {
                return _state.CreateSnapshot(cancellationToken);
            }

            public System.Threading.Tasks.ValueTask RestoreSnapshot(ModuleSnapshot snapshot, System.Threading.CancellationToken cancellationToken = default)
            {
                return _state.RestoreSnapshot(snapshot, cancellationToken);
            }

            public System.Threading.Tasks.ValueTask<ModuleHealthCheckResult> CheckHealth(System.Threading.CancellationToken cancellationToken = default)
            {
                return _state.CheckHealth(cancellationToken);
            }
        }
    }
}
