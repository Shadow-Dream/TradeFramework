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
    /// Configuration root that defines how runtime modules are assembled into a trading pipeline.
    /// </summary>
    public sealed class PipelineManifest
    {
        public string Name { get; }
        public IReadOnlyDictionary<string, ModuleConfiguration> Modules { get; }
        public IReadOnlyList<string> InputModules { get; }
        public IReadOnlyList<string> UniverseModules { get; }
        public IReadOnlyList<string> SignalModules { get; }
        public IReadOnlyList<string> TargetModules { get; }
        public IReadOnlyList<string> ConstraintModules { get; }
        public IReadOnlyList<string> ExecutionModules { get; }
        public string MarketRuleModule { get; }
        public IReadOnlyList<string> AnalyzerModules { get; }

        public PipelineManifest(
            string name,
            IEnumerable<ModuleConfiguration> modules,
            IEnumerable<string> inputModules = null,
            IEnumerable<string> universeModules = null,
            IEnumerable<string> signalModules = null,
            IEnumerable<string> targetModules = null,
            IEnumerable<string> constraintModules = null,
            IEnumerable<string> executionModules = null,
            string marketRuleModule = null,
            IEnumerable<string> analyzerModules = null)
        {
            if (string.IsNullOrWhiteSpace(name))
            {
                throw new ArgumentException("Pipeline manifest name is required.", nameof(name));
            }

            Name = name;
            Modules = BuildModuleDictionary(modules);
            InputModules = Freeze(inputModules);
            UniverseModules = Freeze(universeModules);
            SignalModules = Freeze(signalModules);
            TargetModules = Freeze(targetModules);
            ConstraintModules = Freeze(constraintModules);
            ExecutionModules = Freeze(executionModules);
            MarketRuleModule = marketRuleModule;
            AnalyzerModules = Freeze(analyzerModules);

            Validate();
        }

        public IReadOnlyList<string> ValidateReferences()
        {
            var missing = new List<string>();
            foreach (var key in EnumerateAllReferences())
            {
                if (!Modules.ContainsKey(key))
                {
                    missing.Add(key);
                }
            }

            return Array.AsReadOnly(missing.Distinct().ToArray());
        }

        private void Validate()
        {
            var missing = ValidateReferences();
            if (missing.Count > 0)
            {
                throw new ArgumentException($"Pipeline manifest '{Name}' references unknown modules: {string.Join(", ", missing)}");
            }
        }

        private IEnumerable<string> EnumerateAllReferences()
        {
            foreach (var key in InputModules) yield return key;
            foreach (var key in UniverseModules) yield return key;
            foreach (var key in SignalModules) yield return key;
            foreach (var key in TargetModules) yield return key;
            foreach (var key in ConstraintModules) yield return key;
            foreach (var key in ExecutionModules) yield return key;
            foreach (var key in AnalyzerModules) yield return key;
            if (!string.IsNullOrWhiteSpace(MarketRuleModule)) yield return MarketRuleModule;
        }

        private static IReadOnlyDictionary<string, ModuleConfiguration> BuildModuleDictionary(IEnumerable<ModuleConfiguration> modules)
        {
            if (modules == null)
            {
                throw new ArgumentNullException(nameof(modules));
            }

            var dictionary = new Dictionary<string, ModuleConfiguration>();
            foreach (var module in modules)
            {
                if (dictionary.ContainsKey(module.Key))
                {
                    throw new ArgumentException($"Duplicate module key '{module.Key}' in pipeline manifest.", nameof(modules));
                }

                dictionary[module.Key] = module;
            }

            return new ReadOnlyDictionary<string, ModuleConfiguration>(dictionary);
        }

        private static IReadOnlyList<string> Freeze(IEnumerable<string> values)
        {
            return values == null
                ? Array.Empty<string>()
                : Array.AsReadOnly(values.Where(x => !string.IsNullOrWhiteSpace(x)).Distinct().ToArray());
        }
    }
}
