using System;
using QuantConnect.Modules;
using NUnit.Framework;

namespace QuantConnect.Tests.Common.Modules
{
    [TestFixture]
    public sealed class PipelineManifestTests
    {
        private static readonly string[] AlphaOnly = ["alpha"];
        private static readonly string[] UnknownExecution = ["execution"];
        private static readonly string[] DataDisk = ["data.disk"];
        private static readonly string[] UniverseNdx = ["universe.ndx"];
        private static readonly string[] AlphaTrend = ["alpha.trend"];
        private static readonly string[] TargetMain = ["target.main"];
        private static readonly string[] RiskDd = ["risk.dd"];
        private static readonly string[] ExecImmediate = ["exec.immediate"];
        private static readonly string[] StatsMain = ["stats.main"];

        [Test]
        public void RejectsDuplicateModuleKeys()
        {
            var modules = new[]
            {
                new ModuleConfiguration("alpha", ModuleKind.Signal, ModuleActivationMode.BuiltIn, "Alpha.One"),
                new ModuleConfiguration("alpha", ModuleKind.Signal, ModuleActivationMode.BuiltIn, "Alpha.Two")
            };

            Assert.Throws<ArgumentException>(() => new PipelineManifest("dup", modules));
        }

        [Test]
        public void RejectsUnknownLayoutReferences()
        {
            var modules = new[]
            {
                new ModuleConfiguration("alpha", ModuleKind.Signal, ModuleActivationMode.BuiltIn, "Alpha.One")
            };

            Assert.Throws<ArgumentException>(() => new PipelineManifest(
                "unknown",
                modules,
                signalModules: AlphaOnly,
                executionModules: UnknownExecution));
        }

        [Test]
        public void AcceptsOrderedReferencesAcrossStages()
        {
            var modules = new[]
            {
                new ModuleConfiguration("data.disk", ModuleKind.Input, ModuleActivationMode.BuiltIn, "DefaultDataProvider"),
                new ModuleConfiguration("universe.ndx", ModuleKind.Universe, ModuleActivationMode.ScriptRunner, "universe.py"),
                new ModuleConfiguration("alpha.trend", ModuleKind.Signal, ModuleActivationMode.RemoteService, "grpc://alpha"),
                new ModuleConfiguration("target.main", ModuleKind.Target, ModuleActivationMode.InProcessPlugin, "Target.dll"),
                new ModuleConfiguration("risk.dd", ModuleKind.Constraint, ModuleActivationMode.BuiltIn, "DrawdownRisk"),
                new ModuleConfiguration("exec.immediate", ModuleKind.Execution, ModuleActivationMode.BuiltIn, "ImmediateExecution"),
                new ModuleConfiguration("broker.bitget", ModuleKind.MarketRule, ModuleActivationMode.RemoteService, "grpc://bitget"),
                new ModuleConfiguration("stats.main", ModuleKind.Analyzer, ModuleActivationMode.ScriptRunner, "stats.py")
            };

            var manifest = new PipelineManifest(
                "ndx",
                modules,
                inputModules: DataDisk,
                universeModules: UniverseNdx,
                signalModules: AlphaTrend,
                targetModules: TargetMain,
                constraintModules: RiskDd,
                executionModules: ExecImmediate,
                marketRuleModule: "broker.bitget",
                analyzerModules: StatsMain);

            Assert.That(manifest.SignalModules.Count, Is.EqualTo(1));
            Assert.That(manifest.MarketRuleModule, Is.EqualTo("broker.bitget"));
        }
    }
}
