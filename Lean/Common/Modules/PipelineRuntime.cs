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
using System.Threading;
using System.Threading.Tasks;

namespace QuantConnect.Modules
{
    /// <summary>
    /// Minimal runtime that materializes a configured pipeline into loaded modules.
    /// </summary>
    public sealed class PipelineRuntime
    {
        private readonly IModuleControlPlane _controlPlane;

        public PipelineRuntime(IModuleControlPlane controlPlane)
        {
            _controlPlane = controlPlane ?? throw new ArgumentNullException(nameof(controlPlane));
        }

        public async ValueTask<LoadedPipeline> LoadAsync(PipelineManifest manifest, CancellationToken cancellationToken = default)
        {
            ArgumentNullException.ThrowIfNull(manifest);

            var loaded = new Dictionary<string, IModule>(StringComparer.Ordinal);

            async ValueTask loadRange(IEnumerable<string> keys)
            {
                foreach (var key in keys)
                {
                    if (loaded.ContainsKey(key))
                    {
                        continue;
                    }

                    var configuration = manifest.Modules[key];
                    loaded[key] = await _controlPlane.LoadAsync(configuration, cancellationToken).ConfigureAwait(false);
                }
            }

            await loadRange(manifest.InputModules).ConfigureAwait(false);
            await loadRange(manifest.UniverseModules).ConfigureAwait(false);
            await loadRange(manifest.SignalModules).ConfigureAwait(false);
            await loadRange(manifest.TargetModules).ConfigureAwait(false);
            await loadRange(manifest.ConstraintModules).ConfigureAwait(false);
            await loadRange(manifest.ExecutionModules).ConfigureAwait(false);
            if (!string.IsNullOrWhiteSpace(manifest.MarketRuleModule))
            {
                await loadRange(new[] { manifest.MarketRuleModule }).ConfigureAwait(false);
            }
            await loadRange(manifest.AnalyzerModules).ConfigureAwait(false);

            return new LoadedPipeline(manifest, new ReadOnlyDictionary<string, IModule>(loaded));
        }

        public async ValueTask<LoadedPipeline> ReloadAsync(LoadedPipeline current, PipelineManifest next, CancellationToken cancellationToken = default)
        {
            ArgumentNullException.ThrowIfNull(next);

            current ??= new LoadedPipeline(next, new ReadOnlyDictionary<string, IModule>(new Dictionary<string, IModule>()));
            var loaded = new Dictionary<string, IModule>(current.Modules, StringComparer.Ordinal);

            foreach (var configuration in next.Modules.Values)
            {
                if (loaded.TryGetValue(configuration.Key, out var existing))
                {
                    current.Manifest.Modules.TryGetValue(configuration.Key, out var previousConfiguration);
                    if (ShouldReuse(existing, previousConfiguration, configuration))
                    {
                        continue;
                    }

                    await ReloadModule(configuration.Key, configuration, existing, loaded, cancellationToken).ConfigureAwait(false);
                    continue;
                }

                loaded[configuration.Key] = await _controlPlane.LoadAsync(configuration, cancellationToken).ConfigureAwait(false);
            }

            foreach (var key in new List<string>(loaded.Keys))
            {
                if (!next.Modules.ContainsKey(key))
                {
                    await _controlPlane.UnloadAsync(key, cancellationToken).ConfigureAwait(false);
                    loaded.Remove(key);
                }
            }

            return new LoadedPipeline(next, new ReadOnlyDictionary<string, IModule>(loaded));
        }

        private async ValueTask ReloadModule(
            string key,
            ModuleConfiguration configuration,
            IModule existing,
            Dictionary<string, IModule> loaded,
            CancellationToken cancellationToken)
        {
            ModuleSnapshot snapshot = null;
            var shouldPause = existing.HotSwapMode is ModuleHotSwapMode.RequiresPause or ModuleHotSwapMode.RequiresFlatNoOrders;
            var shouldSnapshot = existing.HotSwapMode is ModuleHotSwapMode.Live or ModuleHotSwapMode.RequiresPause or ModuleHotSwapMode.RequiresFlatNoOrders;
            if (shouldPause)
            {
                await _controlPlane.Pause(key, cancellationToken).ConfigureAwait(false);
            }
            if (shouldSnapshot)
            {
                snapshot = await _controlPlane.SnapshotAsync(key, cancellationToken).ConfigureAwait(false);
            }

            await _controlPlane.UnloadAsync(key, cancellationToken).ConfigureAwait(false);
            var reloaded = await _controlPlane.LoadAsync(configuration, cancellationToken).ConfigureAwait(false);

            if (snapshot != null && configuration.Key == snapshot.ModuleKey)
            {
                await _controlPlane.RestoreAsync(configuration.Key, snapshot, cancellationToken).ConfigureAwait(false);
            }

            if (shouldPause)
            {
                await _controlPlane.Resume(key, cancellationToken).ConfigureAwait(false);
            }

            loaded[key] = reloaded;
        }

        private static bool ShouldReuse(IModule existing, ModuleConfiguration previousConfiguration, ModuleConfiguration nextConfiguration)
        {
            if (previousConfiguration == null)
            {
                return false;
            }

            return existing.Key == nextConfiguration.Key &&
                   existing.Version == nextConfiguration.Version &&
                   existing.ActivationMode == nextConfiguration.ActivationMode &&
                   previousConfiguration.Kind == nextConfiguration.Kind &&
                   previousConfiguration.EntryPoint == nextConfiguration.EntryPoint &&
                   previousConfiguration.HotSwapMode == nextConfiguration.HotSwapMode &&
                   DictionariesEqual(previousConfiguration.Parameters, nextConfiguration.Parameters) &&
                   ListsEqual(previousConfiguration.Dependencies, nextConfiguration.Dependencies);
        }

        private static bool DictionariesEqual(IReadOnlyDictionary<string, string> left, IReadOnlyDictionary<string, string> right)
        {
            if (left.Count != right.Count)
            {
                return false;
            }

            foreach (var kvp in left)
            {
                if (!right.TryGetValue(kvp.Key, out var value) || value != kvp.Value)
                {
                    return false;
                }
            }

            return true;
        }

        private static bool ListsEqual(IReadOnlyList<string> left, IReadOnlyList<string> right)
        {
            if (left.Count != right.Count)
            {
                return false;
            }

            for (var i = 0; i < left.Count; i++)
            {
                if (left[i] != right[i])
                {
                    return false;
                }
            }

            return true;
        }
    }
}
