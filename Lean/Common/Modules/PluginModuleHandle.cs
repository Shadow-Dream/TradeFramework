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
using System.Threading;
using System.Threading.Tasks;

namespace QuantConnect.Modules
{
    /// <summary>
    /// Wraps a module loaded from a collectible assembly load context.
    /// </summary>
    public sealed class PluginModuleHandle : IModule, IDisposable
    {
        private readonly IModule _inner;
        private readonly PluginLoadContext _loadContext;
        private readonly ModuleActivationMode _activationMode;

        internal PluginModuleHandle(IModule inner, PluginLoadContext loadContext, ModuleActivationMode activationMode)
        {
            _inner = inner ?? throw new ArgumentNullException(nameof(inner));
            _loadContext = loadContext ?? throw new ArgumentNullException(nameof(loadContext));
            _activationMode = activationMode;
        }

        public string Key => _inner.Key;
        public ModuleKind Kind => _inner.Kind;
        public ModuleActivationMode ActivationMode => _activationMode;
        public string Version => _inner.Version;
        public ModuleHotSwapMode HotSwapMode => _inner.HotSwapMode;
        public IModule Inner => _inner;

        public ValueTask Initialize(ModuleConfiguration configuration, CancellationToken cancellationToken = default) => _inner.Initialize(configuration, cancellationToken);
        public ValueTask Pause(CancellationToken cancellationToken = default) => _inner.Pause(cancellationToken);
        public ValueTask Resume(CancellationToken cancellationToken = default) => _inner.Resume(cancellationToken);
        public ValueTask<ModuleSnapshot> CreateSnapshot(CancellationToken cancellationToken = default) => _inner.CreateSnapshot(cancellationToken);
        public ValueTask RestoreSnapshot(ModuleSnapshot snapshot, CancellationToken cancellationToken = default) => _inner.RestoreSnapshot(snapshot, cancellationToken);
        public ValueTask<ModuleHealthCheckResult> CheckHealth(CancellationToken cancellationToken = default) => _inner.CheckHealth(cancellationToken);

        public void Dispose()
        {
            _loadContext.Unload();
        }
    }
}
