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
using System.IO;
using System.Threading;
using System.Threading.Tasks;

namespace QuantConnect.Modules
{
    /// <summary>
    /// Creates in-process plugin modules from external assembly paths.
    /// </summary>
    public sealed class ExternalAssemblyModuleFactory : IModuleFactory
    {
        public const string AssemblyPathParameter = "assemblyPath";

        public bool CanCreate(ModuleConfiguration configuration)
        {
            return configuration != null &&
                   configuration.ActivationMode == ModuleActivationMode.InProcessPlugin &&
                   configuration.Parameters.ContainsKey(AssemblyPathParameter);
        }

        public async ValueTask<IModule> CreateAsync(ModuleConfiguration configuration, CancellationToken cancellationToken = default)
        {
            if (!CanCreate(configuration))
            {
                throw new NotSupportedException($"Module activation mode '{configuration?.ActivationMode}' is not supported by {nameof(ExternalAssemblyModuleFactory)}.");
            }

            var assemblyPath = configuration.Parameters[AssemblyPathParameter];
            if (!File.Exists(assemblyPath))
            {
                throw new FileNotFoundException($"Plugin assembly file was not found: {assemblyPath}", assemblyPath);
            }

            var loadContext = new PluginLoadContext(assemblyPath);
            var assembly = loadContext.LoadFromAssemblyPath(Path.GetFullPath(assemblyPath));
            var moduleType = assembly.GetType(configuration.EntryPoint, throwOnError: false)
                ?? throw new InvalidOperationException($"Unable to resolve module type '{configuration.EntryPoint}' from assembly '{assemblyPath}'.");

            var module = ModuleActivator.Create(moduleType, configuration);

            await module.Initialize(configuration, cancellationToken).ConfigureAwait(false);
            return new PluginModuleHandle(module, loadContext, configuration.ActivationMode);
        }
    }
}
