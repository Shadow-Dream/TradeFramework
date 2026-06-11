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
using System.Linq;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Data;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Modules;

namespace QuantConnect.Algorithm.Modules
{
    /// <summary>
    /// Executes a control-plane compiled alpha graph as a single Lean alpha model.
    /// </summary>
    public sealed class CompiledAlphaGraphModule : AlphaModel
    {
        private readonly AlphaGraphManifest _graph;
        private readonly IReadOnlyDictionary<string, IModule> _modules;

        public CompiledAlphaGraphModule(AlphaGraphManifest graph, IReadOnlyDictionary<string, IModule> modules)
        {
            _graph = graph ?? throw new ArgumentNullException(nameof(graph));
            _modules = modules ?? throw new ArgumentNullException(nameof(modules));
            Name = "CompiledAlphaGraph";
        }

        public override IEnumerable<Insight> Update(QCAlgorithm algorithm, Slice data)
        {
            var wires = new Dictionary<string, object>(StringComparer.Ordinal);

            foreach (var nodeId in _graph.Nodes)
            {
                if (!_graph.Bindings.TryGetValue(nodeId, out var binding))
                {
                    throw new InvalidOperationException($"Alpha graph node '{nodeId}' is missing binding metadata.");
                }
                if (!_modules.TryGetValue(nodeId, out var module))
                {
                    throw new InvalidOperationException($"Alpha graph node '{nodeId}' is not loaded.");
                }

                var inputs = ResolveInputs(binding, wires);
                var outputs = EvaluateNode(module, algorithm, data, binding, inputs);
                foreach (var (port, wire) in binding.Outputs)
                {
                    if (!outputs.TryGetValue(port, out var value))
                    {
                        continue;
                    }

                    wires[wire] = value;
                }
            }

            return CollectInsights(wires).ToArray();
        }

        public override void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes)
        {
            foreach (var nodeId in _graph.Nodes)
            {
                if (!_modules.TryGetValue(nodeId, out var module))
                {
                    continue;
                }

                if (module is IAlphaGraphNode graphNode)
                {
                    graphNode.OnSecuritiesChanged(algorithm, changes);
                }
                else if (module is IAlphaModel alphaModel)
                {
                    alphaModel.OnSecuritiesChanged(algorithm, changes);
                }
            }
        }

        private static IReadOnlyDictionary<string, object> ResolveInputs(
            AlphaGraphNodeBinding binding,
            IReadOnlyDictionary<string, object> wires)
        {
            var inputs = new Dictionary<string, object>(StringComparer.Ordinal);
            foreach (var (port, wire) in binding.Inputs)
            {
                if (!wires.TryGetValue(wire, out var value))
                {
                    throw new InvalidOperationException($"Alpha graph node '{binding.InstanceId}' input '{port}' references missing wire '{wire}'.");
                }

                inputs[port] = value;
            }

            return inputs;
        }

        private static IReadOnlyDictionary<string, object> EvaluateNode(
            IModule module,
            QCAlgorithm algorithm,
            Slice data,
            AlphaGraphNodeBinding binding,
            IReadOnlyDictionary<string, object> inputs)
        {
            if (module is IAlphaGraphNode graphNode)
            {
                return graphNode.Evaluate(algorithm, data, binding, inputs) ?? new Dictionary<string, object>();
            }

            if (module is IAlphaModel alphaModel && binding.Outputs.Count == 1)
            {
                var outputPort = binding.Outputs.Keys.First();
                return new Dictionary<string, object>
                {
                    [outputPort] = alphaModel.Update(algorithm, data)?.ToArray() ?? Array.Empty<Insight>()
                };
            }

            throw new InvalidOperationException(
                $"Alpha graph node '{binding.InstanceId}' must implement {nameof(IAlphaGraphNode)}. " +
                "Plain IAlphaModel nodes are only supported when they have exactly one output port.");
        }

        private IEnumerable<Insight> CollectInsights(IReadOnlyDictionary<string, object> wires)
        {
            if (!_graph.Outputs.TryGetValue("insights", out var insightWires))
            {
                yield break;
            }

            foreach (var wire in insightWires)
            {
                if (!wires.TryGetValue(wire, out var value) || value == null)
                {
                    continue;
                }

                if (value is Insight insight)
                {
                    yield return insight;
                    continue;
                }

                if (value is IEnumerable<Insight> insights)
                {
                    foreach (var item in insights)
                    {
                        yield return item;
                    }
                }
            }
        }
    }
}
