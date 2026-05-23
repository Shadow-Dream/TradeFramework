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
using System.Threading;
using System.Threading.Tasks;

namespace QuantConnect.Modules
{
    /// <summary>
    /// Default control plane implementation for loading and managing active modules.
    /// </summary>
    public sealed class ModuleControlPlane : IModuleControlPlane
    {
        private readonly IReadOnlyCollection<IModuleFactory> _factories;
        private readonly Dictionary<string, IModule> _loadedModules = new(StringComparer.Ordinal);
        private readonly object _lock = new();

        public ModuleControlPlane(params IModuleFactory[] factories)
            : this((IReadOnlyCollection<IModuleFactory>)factories)
        {
        }

        public ModuleControlPlane(IReadOnlyCollection<IModuleFactory> factories)
        {
            _factories = factories ?? throw new ArgumentNullException(nameof(factories));
            if (_factories.Count == 0)
            {
                throw new ArgumentException("At least one module factory is required.", nameof(factories));
            }
        }

        public async ValueTask<IModule> LoadAsync(ModuleConfiguration configuration, CancellationToken cancellationToken = default)
        {
            var factory = ResolveFactory(configuration);
            var module = await factory.CreateAsync(configuration, cancellationToken).ConfigureAwait(false);

            lock (_lock)
            {
                _loadedModules[configuration.Key] = module;
            }

            return module;
        }

        public ValueTask UnloadAsync(string key, CancellationToken cancellationToken = default)
        {
            IModule module;
            lock (_lock)
            {
                if (!_loadedModules.TryGetValue(key, out module))
                {
                    return ValueTask.CompletedTask;
                }

                _loadedModules.Remove(key);
            }

            if (module is IDisposable disposable)
            {
                disposable.Dispose();
            }

            return ValueTask.CompletedTask;
        }

        public ValueTask Pause(string key, CancellationToken cancellationToken = default)
        {
            return GetModule(key).Pause(cancellationToken);
        }

        public ValueTask Resume(string key, CancellationToken cancellationToken = default)
        {
            return GetModule(key).Resume(cancellationToken);
        }

        public ValueTask<ModuleSnapshot> SnapshotAsync(string key, CancellationToken cancellationToken = default)
        {
            return GetModule(key).CreateSnapshot(cancellationToken);
        }

        public ValueTask RestoreAsync(string key, ModuleSnapshot snapshot, CancellationToken cancellationToken = default)
        {
            return GetModule(key).RestoreSnapshot(snapshot, cancellationToken);
        }

        public ValueTask<ModuleHealthCheckResult> CheckHealth(string key, CancellationToken cancellationToken = default)
        {
            return GetModule(key).CheckHealth(cancellationToken);
        }

        private IModuleFactory ResolveFactory(ModuleConfiguration configuration)
        {
            foreach (var factory in _factories)
            {
                if (factory.CanCreate(configuration))
                {
                    return factory;
                }
            }

            throw new NotSupportedException($"No module factory can create activation mode '{configuration.ActivationMode}'.");
        }

        private IModule GetModule(string key)
        {
            lock (_lock)
            {
                if (_loadedModules.TryGetValue(key, out var module))
                {
                    return module;
                }
            }

            throw new KeyNotFoundException($"Module '{key}' is not loaded.");
        }
    }
}
