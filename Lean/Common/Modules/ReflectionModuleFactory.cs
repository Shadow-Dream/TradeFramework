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
using System.Linq;
using System.Threading;
using System.Threading.Tasks;

namespace QuantConnect.Modules
{
    /// <summary>
    /// Creates built-in modules from reflection-visible type names already loaded in the current AppDomain.
    /// </summary>
    public sealed class ReflectionModuleFactory : IModuleFactory
    {
        public bool CanCreate(ModuleConfiguration configuration)
        {
            return configuration != null &&
                   configuration.ActivationMode == ModuleActivationMode.BuiltIn;
        }

        public async ValueTask<IModule> CreateAsync(ModuleConfiguration configuration, CancellationToken cancellationToken = default)
        {
            if (!CanCreate(configuration))
            {
                throw new NotSupportedException($"Module activation mode '{configuration?.ActivationMode}' is not supported by {nameof(ReflectionModuleFactory)}.");
            }

            var moduleType = ResolveType(configuration.EntryPoint);
            if (moduleType == null)
            {
                throw new InvalidOperationException($"Unable to resolve module type '{configuration.EntryPoint}'.");
            }

            var module = ModuleActivator.Create(moduleType, configuration);

            await module.Initialize(configuration, cancellationToken).ConfigureAwait(false);
            return module;
        }

        private static Type ResolveType(string entryPoint)
        {
            var type = Type.GetType(entryPoint, throwOnError: false);
            if (type != null)
            {
                return type;
            }

            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                type = assembly.GetType(entryPoint, throwOnError: false);
                if (type != null)
                {
                    return type;
                }
            }

            return AppDomain.CurrentDomain
                .GetAssemblies()
                .Where(assembly => !assembly.IsDynamic)
                .SelectMany(assembly => assembly.GetTypes())
                .SingleOrDefault(typeInfo => typeInfo.FullName == entryPoint || typeInfo.Name == entryPoint);
        }
    }
}
