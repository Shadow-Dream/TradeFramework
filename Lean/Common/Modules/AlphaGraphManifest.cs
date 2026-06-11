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
using System.Collections.ObjectModel;
using System.Linq;

namespace QuantConnect.Modules
{
    /// <summary>
    /// Describes a typed alpha-node graph compiled by the control plane.
    /// </summary>
    public sealed class AlphaGraphManifest
    {
        public static AlphaGraphManifest Empty { get; } = new(
            Array.Empty<string>(),
            new Dictionary<string, IReadOnlyList<string>>(),
            new Dictionary<string, AlphaGraphNodeBinding>(),
            Array.Empty<AlphaGraphEdge>());

        public IReadOnlyList<string> Nodes { get; }
        public IReadOnlyDictionary<string, IReadOnlyList<string>> Outputs { get; }
        public IReadOnlyDictionary<string, AlphaGraphNodeBinding> Bindings { get; }
        public IReadOnlyList<AlphaGraphEdge> Edges { get; }

        public bool HasNodes => Nodes.Count > 0;

        public AlphaGraphManifest(
            IEnumerable<string> nodes,
            IReadOnlyDictionary<string, IReadOnlyList<string>> outputs,
            IReadOnlyDictionary<string, AlphaGraphNodeBinding> bindings,
            IEnumerable<AlphaGraphEdge> edges)
        {
            Nodes = FreezeList(nodes);
            Outputs = FreezeStringListDictionary(outputs);
            Bindings = new ReadOnlyDictionary<string, AlphaGraphNodeBinding>(
                new Dictionary<string, AlphaGraphNodeBinding>(bindings ?? new Dictionary<string, AlphaGraphNodeBinding>(), StringComparer.Ordinal));
            Edges = FreezeList(edges);
        }

        private static IReadOnlyList<T> FreezeList<T>(IEnumerable<T> values)
        {
            return values == null
                ? Array.Empty<T>()
                : Array.AsReadOnly(values.Where(x => x != null).ToArray());
        }

        private static IReadOnlyDictionary<string, IReadOnlyList<string>> FreezeStringListDictionary(IReadOnlyDictionary<string, IReadOnlyList<string>> values)
        {
            var result = new Dictionary<string, IReadOnlyList<string>>(StringComparer.Ordinal);
            foreach (var (key, value) in values ?? new Dictionary<string, IReadOnlyList<string>>())
            {
                if (string.IsNullOrWhiteSpace(key))
                {
                    continue;
                }

                result[key] = FreezeList(value);
            }

            return new ReadOnlyDictionary<string, IReadOnlyList<string>>(result);
        }
    }

    /// <summary>
    /// Captures one alpha graph node's module identity, config and wire bindings.
    /// </summary>
    public sealed class AlphaGraphNodeBinding
    {
        public string InstanceId { get; }
        public string ModuleId { get; }
        public string Version { get; }
        public IReadOnlyDictionary<string, string> Inputs { get; }
        public IReadOnlyDictionary<string, string> Outputs { get; }
        public IReadOnlyDictionary<string, string> Config { get; }

        public AlphaGraphNodeBinding(
            string instanceId,
            string moduleId,
            string version,
            IReadOnlyDictionary<string, string> inputs,
            IReadOnlyDictionary<string, string> outputs,
            IReadOnlyDictionary<string, string> config)
        {
            InstanceId = instanceId ?? string.Empty;
            ModuleId = moduleId ?? string.Empty;
            Version = version ?? string.Empty;
            Inputs = FreezeDictionary(inputs);
            Outputs = FreezeDictionary(outputs);
            Config = FreezeDictionary(config);
        }

        private static IReadOnlyDictionary<string, string> FreezeDictionary(IReadOnlyDictionary<string, string> values)
        {
            return new ReadOnlyDictionary<string, string>(
                new Dictionary<string, string>(values ?? new Dictionary<string, string>(), StringComparer.Ordinal));
        }
    }

    /// <summary>
    /// Describes one typed wire from a producing node port to a consuming node port.
    /// </summary>
    public sealed class AlphaGraphEdge
    {
        public string Wire { get; }
        public AlphaGraphEndpoint From { get; }
        public AlphaGraphEndpoint To { get; }

        public AlphaGraphEdge(string wire, AlphaGraphEndpoint from, AlphaGraphEndpoint to)
        {
            Wire = wire ?? string.Empty;
            From = from;
            To = to;
        }
    }

    /// <summary>
    /// Identifies one endpoint of an alpha graph wire.
    /// </summary>
    public sealed class AlphaGraphEndpoint
    {
        public string Node { get; }
        public string Port { get; }
        public string Type { get; }

        public AlphaGraphEndpoint(string node, string port, string type)
        {
            Node = node ?? string.Empty;
            Port = port ?? string.Empty;
            Type = type ?? "any";
        }
    }
}
