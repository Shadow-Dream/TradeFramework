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
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;

namespace QuantConnect.Modules
{
    /// <summary>
    /// Reusable runtime lifecycle state for built-in module implementations.
    /// </summary>
    public sealed class ModuleState
    {
        public string Key { get; private set; }
        public ModuleKind Kind { get; }
        public ModuleActivationMode ActivationMode => ModuleActivationMode.BuiltIn;
        public string Version { get; }
        public ModuleHotSwapMode HotSwapMode { get; }

        public ModuleState(Type moduleType, ModuleKind kind, ModuleHotSwapMode hotSwapMode)
        {
            if (moduleType == null)
            {
                throw new ArgumentNullException(nameof(moduleType));
            }

            Key = moduleType.FullName ?? moduleType.Name;
            Kind = kind;
            HotSwapMode = hotSwapMode;
            Version = moduleType.Assembly.GetName().Version?.ToString() ?? Assembly.GetExecutingAssembly().GetName().Version?.ToString() ?? "0.0.0";
        }

        public ValueTask Initialize(ModuleConfiguration configuration, CancellationToken cancellationToken = default)
        {
            if (configuration != null && !string.IsNullOrWhiteSpace(configuration.Key))
            {
                Key = configuration.Key;
            }

            return ValueTask.CompletedTask;
        }

        public ValueTask Pause(CancellationToken cancellationToken = default)
        {
            return ValueTask.CompletedTask;
        }

        public ValueTask Resume(CancellationToken cancellationToken = default)
        {
            return ValueTask.CompletedTask;
        }

        public ValueTask<ModuleSnapshot> CreateSnapshot(CancellationToken cancellationToken = default)
        {
            return ValueTask.FromResult(new ModuleSnapshot(Key, Version, Array.Empty<byte>(), "application/x.quantconnect.empty-snapshot"));
        }

        public ValueTask RestoreSnapshot(ModuleSnapshot snapshot, CancellationToken cancellationToken = default)
        {
            if (snapshot.ModuleKey != Key)
            {
                throw new InvalidOperationException($"Snapshot key '{snapshot.ModuleKey}' does not match module key '{Key}'.");
            }

            return ValueTask.CompletedTask;
        }

        public ValueTask<ModuleHealthCheckResult> CheckHealth(CancellationToken cancellationToken = default)
        {
            return ValueTask.FromResult(new ModuleHealthCheckResult(ModuleHealthStatus.Healthy));
        }
    }
}
