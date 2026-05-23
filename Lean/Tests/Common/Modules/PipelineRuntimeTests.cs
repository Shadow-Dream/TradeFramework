using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using QuantConnect.Modules;
using NUnit.Framework;

namespace QuantConnect.Tests.Common.Modules
{
    [TestFixture]
    public sealed class PipelineRuntimeTests
    {
        private static readonly string[] DataOne = ["data.one"];
        private static readonly string[] UniverseOne = ["universe.one"];
        private static readonly string[] SignalOne = ["signal.one"];
        private static readonly string[] TargetOne = ["target.one"];
        private static readonly string[] ConstraintOne = ["constraint.one"];
        private static readonly string[] ExecutionOne = ["execution.one"];
        private static readonly string[] AnalyzerOne = ["analyzer.one"];

        [Test]
        public async Task LoadsModulesInManifestStages()
        {
            var modules = new[]
            {
                new ModuleConfiguration("data.one", ModuleKind.Input, ModuleActivationMode.BuiltIn, typeof(DataTestModule).FullName),
                new ModuleConfiguration("universe.one", ModuleKind.Universe, ModuleActivationMode.BuiltIn, typeof(UniverseTestModule).FullName),
                new ModuleConfiguration("signal.one", ModuleKind.Signal, ModuleActivationMode.BuiltIn, typeof(SignalTestModule).FullName),
                new ModuleConfiguration("target.one", ModuleKind.Target, ModuleActivationMode.BuiltIn, typeof(TargetTestModule).FullName),
                new ModuleConfiguration("constraint.one", ModuleKind.Constraint, ModuleActivationMode.BuiltIn, typeof(ConstraintTestModule).FullName),
                new ModuleConfiguration("execution.one", ModuleKind.Execution, ModuleActivationMode.BuiltIn, typeof(ExecutionTestModule).FullName),
                new ModuleConfiguration("market.one", ModuleKind.MarketRule, ModuleActivationMode.BuiltIn, typeof(MarketRuleTestModule).FullName),
                new ModuleConfiguration("analyzer.one", ModuleKind.Analyzer, ModuleActivationMode.BuiltIn, typeof(AnalyzerTestModule).FullName)
            };

            var manifest = new PipelineManifest(
                "smoke",
                modules,
                inputModules: DataOne,
                universeModules: UniverseOne,
                signalModules: SignalOne,
                targetModules: TargetOne,
                constraintModules: ConstraintOne,
                executionModules: ExecutionOne,
                marketRuleModule: "market.one",
                analyzerModules: AnalyzerOne);

            var runtime = new PipelineRuntime(new ModuleControlPlane(new ReflectionModuleFactory()));
            var loaded = await runtime.LoadAsync(manifest).ConfigureAwait(false);

            Assert.That(loaded.Modules.Count, Is.EqualTo(8));
            Assert.That(loaded.Modules.ContainsKey("execution.one"), Is.True);
            Assert.That(loaded.Modules["market.one"].Kind, Is.EqualTo(ModuleKind.MarketRule));
        }

        [Test]
        public async Task ReusesSingleLoadedInstancePerManifestKey()
        {
            var modules = new[]
            {
                new ModuleConfiguration("signal.one", ModuleKind.Signal, ModuleActivationMode.BuiltIn, typeof(SignalTestModule).FullName)
            };

            var manifest = new PipelineManifest(
                "dedupe",
                modules,
                signalModules: SignalOne,
                analyzerModules: SignalOne);

            var runtime = new PipelineRuntime(new ModuleControlPlane(new ReflectionModuleFactory()));
            var loaded = await runtime.LoadAsync(manifest).ConfigureAwait(false);

            Assert.That(loaded.Modules.Count, Is.EqualTo(1));
        }

        private abstract class TestModuleBase : IModule
        {
            private readonly ModuleState _state;

            protected TestModuleBase(ModuleKind kind)
            {
                _state = new ModuleState(GetType(), kind, ModuleHotSwapMode.Live);
            }

            public string Key => _state.Key;
            public ModuleKind Kind => _state.Kind;
            public ModuleActivationMode ActivationMode => _state.ActivationMode;
            public string Version => _state.Version;
            public ModuleHotSwapMode HotSwapMode => _state.HotSwapMode;

            public ValueTask Initialize(ModuleConfiguration configuration, CancellationToken cancellationToken = default)
            {
                return _state.Initialize(configuration, cancellationToken);
            }

            public ValueTask Pause(CancellationToken cancellationToken = default)
            {
                return _state.Pause(cancellationToken);
            }

            public ValueTask Resume(CancellationToken cancellationToken = default)
            {
                return _state.Resume(cancellationToken);
            }

            public ValueTask<ModuleSnapshot> CreateSnapshot(CancellationToken cancellationToken = default)
            {
                return _state.CreateSnapshot(cancellationToken);
            }

            public ValueTask RestoreSnapshot(ModuleSnapshot snapshot, CancellationToken cancellationToken = default)
            {
                return _state.RestoreSnapshot(snapshot, cancellationToken);
            }

            public ValueTask<ModuleHealthCheckResult> CheckHealth(CancellationToken cancellationToken = default)
            {
                return _state.CheckHealth(cancellationToken);
            }
        }

        private sealed class DataTestModule : TestModuleBase { public DataTestModule() : base(ModuleKind.Input) { } }
        private sealed class UniverseTestModule : TestModuleBase { public UniverseTestModule() : base(ModuleKind.Universe) { } }
        private sealed class SignalTestModule : TestModuleBase { public SignalTestModule() : base(ModuleKind.Signal) { } }
        private sealed class TargetTestModule : TestModuleBase { public TargetTestModule() : base(ModuleKind.Target) { } }
        private sealed class ConstraintTestModule : TestModuleBase { public ConstraintTestModule() : base(ModuleKind.Constraint) { } }
        private sealed class ExecutionTestModule : TestModuleBase { public ExecutionTestModule() : base(ModuleKind.Execution) { } }
        private sealed class MarketRuleTestModule : TestModuleBase { public MarketRuleTestModule() : base(ModuleKind.MarketRule) { } }
        private sealed class AnalyzerTestModule : TestModuleBase { public AnalyzerTestModule() : base(ModuleKind.Analyzer) { } }
    }
}
