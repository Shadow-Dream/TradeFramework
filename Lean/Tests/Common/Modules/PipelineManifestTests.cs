using System;
using System.IO;
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

        [Test]
        public void PipelineLanesManifestJsonLoaderComposesActiveLanes()
        {
            var root = TestContext.CurrentContext.WorkDirectory;
            var laneOne = Path.Combine(root, "lane-one-pipeline.json");
            var laneTwo = Path.Combine(root, "lane-two-pipeline.json");
            var lanes = Path.Combine(root, "pipeline-lanes.json");
            var manifest = """
            {
              "name": "lane",
              "modules": [
                {"key":"input","kind":"Input","activationMode":"BuiltIn","entryPoint":"InputEntry","version":"1","hotSwapMode":"Live"},
                {"key":"alpha","kind":"Signal","activationMode":"BuiltIn","entryPoint":"AlphaEntry","version":"1","hotSwapMode":"Live"},
                {"key":"target","kind":"Target","activationMode":"BuiltIn","entryPoint":"TargetEntry","version":"1","hotSwapMode":"Live"},
                {"key":"exec","kind":"Execution","activationMode":"BuiltIn","entryPoint":"ExecEntry","version":"1","hotSwapMode":"RequiresPause"}
              ],
              "inputs":["input"],
              "signal":["alpha"],
              "target":["target"],
              "execution":["exec"]
            }
            """;

            File.WriteAllText(laneOne, manifest.Replace("\"name\": \"lane\"", "\"name\": \"lane-one\""));
            File.WriteAllText(laneTwo, manifest.Replace("\"name\": \"lane\"", "\"name\": \"lane-two\""));
            File.WriteAllText(lanes, $$"""
            {
              "schemaVersion": 1,
              "lanes": [
                {"laneId":"main","manifestPath":"{{laneOne}}"},
                {"laneId":"research","manifestPath":"{{laneTwo}}"}
              ]
            }
            """);

            var composed = PipelineLanesManifestJsonLoader.Load(lanes);

            Assert.That(composed.InputModules, Is.EquivalentTo(new[] { "main::input", "research::input" }));
            Assert.That(composed.SignalModules, Is.EquivalentTo(new[] { "main::alpha", "research::alpha" }));
            Assert.That(composed.TargetModules, Is.EquivalentTo(new[] { "main::target", "research::target" }));
            Assert.That(composed.ExecutionModules, Is.EqualTo(new[] { "main::exec" }));
        }

        [Test]
        public void PipelineLanesManifestJsonLoaderRejectsConflictingExecutionModules()
        {
            var root = TestContext.CurrentContext.WorkDirectory;
            var laneOne = Path.Combine(root, "lane-one-execution.json");
            var laneTwo = Path.Combine(root, "lane-two-execution.json");
            var lanes = Path.Combine(root, "pipeline-lanes-conflict.json");
            var manifest = """
            {
              "name": "lane",
              "modules": [
                {"key":"target","kind":"Target","activationMode":"BuiltIn","entryPoint":"TargetEntry","version":"1","hotSwapMode":"Live"},
                {"key":"exec","kind":"Execution","activationMode":"BuiltIn","entryPoint":"ExecEntry","version":"1","hotSwapMode":"RequiresPause"}
              ],
              "target":["target"],
              "execution":["exec"]
            }
            """;

            File.WriteAllText(laneOne, manifest);
            File.WriteAllText(laneTwo, manifest.Replace("ExecEntry", "OtherExecEntry"));
            File.WriteAllText(lanes, $$"""
            {
              "schemaVersion": 1,
              "lanes": [
                {"laneId":"main","manifestPath":"{{laneOne}}"},
                {"laneId":"research","manifestPath":"{{laneTwo}}"}
              ]
            }
            """);

            Assert.Throws<NotSupportedException>(() => PipelineLanesManifestJsonLoader.Load(lanes));
        }
    }
}
