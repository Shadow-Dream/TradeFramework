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
    /// Immutable manifest entry describing how a module should be loaded.
    /// </summary>
    public sealed class ModuleConfiguration
    {
        public string Key { get; }
        public ModuleKind Kind { get; }
        public ModuleActivationMode ActivationMode { get; }
        public string EntryPoint { get; }
        public string Version { get; }
        public ModuleHotSwapMode HotSwapMode { get; }
        public IReadOnlyDictionary<string, string> Parameters { get; }
        public IReadOnlyList<string> Dependencies { get; }

        public ModuleConfiguration(
            string key,
            ModuleKind kind,
            ModuleActivationMode activationMode,
            string entryPoint,
            string version = "1.0.0",
            ModuleHotSwapMode hotSwapMode = ModuleHotSwapMode.RequiresPause,
            IReadOnlyDictionary<string, string> parameters = null,
            IReadOnlyList<string> dependencies = null)
        {
            if (string.IsNullOrWhiteSpace(key))
            {
                throw new ArgumentException("Module key is required.", nameof(key));
            }

            if (string.IsNullOrWhiteSpace(entryPoint))
            {
                throw new ArgumentException("Module entry point is required.", nameof(entryPoint));
            }

            Key = key;
            Kind = kind;
            ActivationMode = activationMode;
            EntryPoint = entryPoint;
            Version = version;
            HotSwapMode = hotSwapMode;
            Parameters = parameters == null
                ? new ReadOnlyDictionary<string, string>(new Dictionary<string, string>())
                : new ReadOnlyDictionary<string, string>(new Dictionary<string, string>(parameters));
            Dependencies = dependencies == null
                ? Array.Empty<string>()
                : Array.AsReadOnly(dependencies.Where(x => !string.IsNullOrWhiteSpace(x)).Distinct().ToArray());
        }
    }
}
