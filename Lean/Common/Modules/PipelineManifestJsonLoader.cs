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
    /// Loads a <see cref="PipelineManifest"/> from a JSON file.
    /// </summary>
    public static class PipelineManifestJsonLoader
    {
        public static PipelineManifest Load(string path)
        {
            if (string.IsNullOrWhiteSpace(path))
            {
                throw new ArgumentException("Pipeline manifest path is required.", nameof(path));
            }

            if (!File.Exists(path))
            {
                throw new FileNotFoundException($"Pipeline manifest file was not found: {path}", path);
            }

            var root = JObject.Parse(File.ReadAllText(path));
            var modules = root["modules"]?.Select(ParseModule).ToArray()
                ?? throw new ArgumentException("Pipeline manifest requires a 'modules' array.", nameof(path));

            return new PipelineManifest(
                (string)root["name"] ?? Path.GetFileNameWithoutExtension(path),
                modules,
                ParseStage(root, "inputs"),
                ParseStage(root, "universe"),
                ParseStage(root, "signal"),
                ParseStage(root, "target"),
                ParseStage(root, "constraint"),
                ParseStage(root, "execution"),
                (string)root["marketRule"],
                ParseStage(root, "analyzer"),
                ParseAlphaGraph(root["alphaGraph"]));
        }

        private static ModuleConfiguration ParseModule(JToken token)
        {
            var parameters = token["parameters"]?.Children<JProperty>()
                .ToDictionary(x => x.Name, x => (string)x.Value ?? string.Empty)
                ?? new Dictionary<string, string>();
            var dependencies = token["dependencies"]?.Values<string>().ToArray() ?? Array.Empty<string>();

            return new ModuleConfiguration(
                (string)token["key"],
                Enum.Parse<ModuleKind>((string)token["kind"], true),
                Enum.Parse<ModuleActivationMode>((string)token["activationMode"], true),
                (string)token["entryPoint"],
                (string)token["version"] ?? "1.0.0",
                token["hotSwapMode"] != null
                    ? Enum.Parse<ModuleHotSwapMode>((string)token["hotSwapMode"], true)
                    : throw new ArgumentException("Pipeline manifest requires each module to declare 'hotSwapMode'."),
                parameters,
                dependencies);
        }

        private static IReadOnlyList<string> ParseStage(JObject root, string key)
        {
            return root[key]?.Values<string>().Where(x => !string.IsNullOrWhiteSpace(x)).ToArray()
                ?? Array.Empty<string>();
        }

        private static AlphaGraphManifest ParseAlphaGraph(JToken token)
        {
            if (token == null || token.Type == JTokenType.Null)
            {
                return AlphaGraphManifest.Empty;
            }

            var root = (JObject)token;
            var nodes = root["nodes"]?.Values<string>().Where(x => !string.IsNullOrWhiteSpace(x)).ToArray()
                ?? Array.Empty<string>();
            var outputs = ParseStringListDictionary(root["outputs"] as JObject);
            var bindings = ParseBindings(root["bindings"] as JObject);
            var edges = (root["edges"] as JArray)?.Select(ParseEdge).ToArray()
                ?? Array.Empty<AlphaGraphEdge>();

            return new AlphaGraphManifest(nodes, outputs, bindings, edges);
        }

        private static IReadOnlyDictionary<string, IReadOnlyList<string>> ParseStringListDictionary(JObject root)
        {
            var result = new Dictionary<string, IReadOnlyList<string>>();
            if (root == null)
            {
                return result;
            }

            foreach (var property in root.Properties())
            {
                result[property.Name] = property.Value.Values<string>().Where(x => !string.IsNullOrWhiteSpace(x)).ToArray();
            }

            return result;
        }

        private static IReadOnlyDictionary<string, AlphaGraphNodeBinding> ParseBindings(JObject root)
        {
            var result = new Dictionary<string, AlphaGraphNodeBinding>();
            if (root == null)
            {
                return result;
            }

            foreach (var property in root.Properties())
            {
                var value = (JObject)property.Value;
                result[property.Name] = new AlphaGraphNodeBinding(
                    (string)value["instanceId"] ?? property.Name,
                    (string)value["moduleId"],
                    (string)value["version"],
                    ParseStringDictionary(value["inputs"] as JObject),
                    ParseStringDictionary(value["outputs"] as JObject),
                    ParseValueDictionary(value["config"] as JObject));
            }

            return result;
        }

        private static IReadOnlyDictionary<string, string> ParseStringDictionary(JObject root)
        {
            var result = new Dictionary<string, string>();
            if (root == null)
            {
                return result;
            }

            foreach (var property in root.Properties())
            {
                result[property.Name] = (string)property.Value ?? string.Empty;
            }

            return result;
        }

        private static IReadOnlyDictionary<string, string> ParseValueDictionary(JObject root)
        {
            var result = new Dictionary<string, string>();
            if (root == null)
            {
                return result;
            }

            foreach (var property in root.Properties())
            {
                result[property.Name] = property.Value.Type == JTokenType.String
                    ? (string)property.Value
                    : property.Value.ToString(Newtonsoft.Json.Formatting.None);
            }

            return result;
        }

        private static AlphaGraphEdge ParseEdge(JToken token)
        {
            return new AlphaGraphEdge(
                (string)token["wire"],
                ParseEndpoint(token["from"]),
                ParseEndpoint(token["to"]));
        }

        private static AlphaGraphEndpoint ParseEndpoint(JToken token)
        {
            return new AlphaGraphEndpoint(
                (string)token?["node"],
                (string)token?["port"],
                (string)token?["type"]);
        }
    }
}
