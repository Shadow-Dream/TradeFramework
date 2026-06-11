/*
 * QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
 * Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;

namespace QuantConnect.Modules
{
    /// <summary>
    /// Loads a lane registry and composes every active lane manifest into one Engine pipeline.
    /// </summary>
    public static class PipelineLanesManifestJsonLoader
    {
        public static PipelineManifest Load(string path)
        {
            if (string.IsNullOrWhiteSpace(path))
            {
                throw new ArgumentException("Pipeline lanes manifest path is required.", nameof(path));
            }

            if (!File.Exists(path))
            {
                throw new FileNotFoundException($"Pipeline lanes manifest file was not found: {path}", path);
            }

            var root = JObject.Parse(File.ReadAllText(path));
            var baseDirectory = Path.GetDirectoryName(Path.GetFullPath(path)) ?? Directory.GetCurrentDirectory();
            var lanes = root["lanes"]?.Children<JObject>()
                .Select(token => new LaneManifestReference(
                    (string)token["laneId"],
                    ResolvePath(baseDirectory, (string)token["manifestPath"])))
                .Where(lane => !string.IsNullOrWhiteSpace(lane.LaneId) && !string.IsNullOrWhiteSpace(lane.ManifestPath))
                .ToArray() ?? Array.Empty<LaneManifestReference>();

            if (lanes.Length == 0)
            {
                throw new ArgumentException($"Pipeline lanes manifest '{path}' does not contain active lanes.", nameof(path));
            }

            return Compose(lanes);
        }

        private static PipelineManifest Compose(IEnumerable<LaneManifestReference> lanes)
        {
            var modules = new List<ModuleConfiguration>();
            var inputs = new List<string>();
            var universe = new List<string>();
            var signal = new List<string>();
            var target = new List<string>();
            var constraint = new List<string>();
            var analyzer = new List<string>();
            var alphaNodes = new List<string>();
            var alphaOutputs = new Dictionary<string, IReadOnlyList<string>>(StringComparer.Ordinal);
            var alphaBindings = new Dictionary<string, AlphaGraphNodeBinding>(StringComparer.Ordinal);
            var alphaEdges = new List<AlphaGraphEdge>();
            var executionCandidates = new List<(ModuleConfiguration Config, string ScopedKey)>();
            var marketRuleCandidates = new List<(ModuleConfiguration Config, string ScopedKey)>();
            var execution = new List<string>();
            string marketRule = null;

            foreach (var lane in lanes)
            {
                var manifest = PipelineManifestJsonLoader.Load(lane.ManifestPath);
                var keyMap = manifest.Modules.Keys.ToDictionary(key => key, key => ScopedKey(lane.LaneId, key), StringComparer.Ordinal);

                foreach (var module in manifest.Modules.Values)
                {
                    modules.Add(CloneModule(module, keyMap[module.Key], keyMap));
                }

                inputs.AddRange(Remap(manifest.InputModules, keyMap));
                universe.AddRange(Remap(manifest.UniverseModules, keyMap));
                signal.AddRange(Remap(manifest.SignalModules, keyMap));
                target.AddRange(Remap(manifest.TargetModules, keyMap));
                constraint.AddRange(Remap(manifest.ConstraintModules, keyMap));
                analyzer.AddRange(Remap(manifest.AnalyzerModules, keyMap));

                foreach (var key in manifest.ExecutionModules)
                {
                    if (manifest.Modules.TryGetValue(key, out var module))
                    {
                        executionCandidates.Add((module, keyMap[key]));
                    }
                }

                if (!string.IsNullOrWhiteSpace(manifest.MarketRuleModule) &&
                    manifest.Modules.TryGetValue(manifest.MarketRuleModule, out var marketRuleModule))
                {
                    marketRuleCandidates.Add((marketRuleModule, keyMap[manifest.MarketRuleModule]));
                }

                MergeAlphaGraph(lane.LaneId, manifest.AlphaGraph, keyMap, alphaNodes, alphaOutputs, alphaBindings, alphaEdges);
            }

            var distinctExecution = DistinctConfigurations(executionCandidates.Select(x => x.Config)).ToArray();
            if (distinctExecution.Length > 1)
            {
                throw new NotSupportedException("Multiple active lanes must share one execution configuration; execution coordination is not yet split per lane.");
            }
            if (executionCandidates.Count > 0)
            {
                execution.Add(executionCandidates[0].ScopedKey);
            }

            var distinctMarketRules = DistinctConfigurations(marketRuleCandidates.Select(x => x.Config)).ToArray();
            if (distinctMarketRules.Length > 1)
            {
                throw new NotSupportedException("Multiple active lanes must share one market rule configuration because Lean exposes one brokerage model per algorithm.");
            }
            if (marketRuleCandidates.Count > 0)
            {
                marketRule = marketRuleCandidates[0].ScopedKey;
            }

            return new PipelineManifest(
                "multi-lane",
                modules,
                inputs,
                universe,
                signal,
                target,
                constraint,
                execution,
                marketRule,
                analyzer,
                new AlphaGraphManifest(alphaNodes, alphaOutputs, alphaBindings, alphaEdges));
        }

        private static void MergeAlphaGraph(
            string laneId,
            AlphaGraphManifest graph,
            IReadOnlyDictionary<string, string> keyMap,
            ICollection<string> nodes,
            IDictionary<string, IReadOnlyList<string>> outputs,
            IDictionary<string, AlphaGraphNodeBinding> bindings,
            ICollection<AlphaGraphEdge> edges)
        {
            if (!graph.HasNodes)
            {
                return;
            }

            foreach (var node in graph.Nodes)
            {
                nodes.Add(keyMap[node]);
            }

            foreach (var (name, wires) in graph.Outputs)
            {
                var outputName = name == "insights" ? name : ScopedWire(laneId, name);
                var scopedWires = wires.Select(wire => ScopedWire(laneId, wire)).ToArray();
                outputs[outputName] = outputs.TryGetValue(outputName, out var existing)
                    ? existing.Concat(scopedWires).ToArray()
                    : scopedWires;
            }

            foreach (var (node, binding) in graph.Bindings)
            {
                bindings[keyMap[node]] = new AlphaGraphNodeBinding(
                    ScopedKey(laneId, binding.InstanceId),
                    binding.ModuleId,
                    binding.Version,
                    binding.Inputs.ToDictionary(x => x.Key, x => ScopedWire(laneId, x.Value), StringComparer.Ordinal),
                    binding.Outputs.ToDictionary(x => x.Key, x => ScopedWire(laneId, x.Value), StringComparer.Ordinal),
                    binding.Config);
            }

            foreach (var edge in graph.Edges)
            {
                edges.Add(new AlphaGraphEdge(
                    ScopedWire(laneId, edge.Wire),
                    new AlphaGraphEndpoint(keyMap[edge.From.Node], edge.From.Port, edge.From.Type),
                    new AlphaGraphEndpoint(keyMap[edge.To.Node], edge.To.Port, edge.To.Type)));
            }
        }

        private static IEnumerable<string> Remap(IEnumerable<string> values, IReadOnlyDictionary<string, string> keyMap)
        {
            foreach (var value in values)
            {
                yield return keyMap[value];
            }
        }

        private static ModuleConfiguration CloneModule(
            ModuleConfiguration module,
            string key,
            IReadOnlyDictionary<string, string> keyMap)
        {
            return new ModuleConfiguration(
                key,
                module.Kind,
                module.ActivationMode,
                module.EntryPoint,
                module.Version,
                module.HotSwapMode,
                module.Parameters,
                module.Dependencies.Select(dependency => keyMap.TryGetValue(dependency, out var mapped) ? mapped : dependency).ToArray());
        }

        private static IEnumerable<ModuleConfiguration> DistinctConfigurations(IEnumerable<ModuleConfiguration> modules)
        {
            return modules
                .GroupBy(ConfigurationSignature)
                .Select(group => group.First());
        }

        private static string ConfigurationSignature(ModuleConfiguration module)
        {
            var parameters = string.Join("|", module.Parameters.OrderBy(x => x.Key).Select(x => $"{x.Key}={x.Value}"));
            var dependencies = string.Join("|", module.Dependencies.OrderBy(x => x));
            return $"{module.Kind}:{module.ActivationMode}:{module.EntryPoint}:{module.Version}:{module.HotSwapMode}:{parameters}:{dependencies}";
        }

        private static string ScopedKey(string laneId, string key)
        {
            return $"{laneId}::{key}";
        }

        private static string ScopedWire(string laneId, string wire)
        {
            return $"{laneId}::{wire}";
        }

        private static string ResolvePath(string baseDirectory, string path)
        {
            return Path.IsPathRooted(path) ? path : Path.GetFullPath(Path.Combine(baseDirectory, path));
        }

        private sealed record LaneManifestReference(string LaneId, string ManifestPath);
    }
}
