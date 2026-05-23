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
using QuantConnect.Modules;

namespace QuantConnect.Algorithm.Modules
{
    public sealed class RemoteServiceModuleFactory : IModuleFactory
    {
        public const string BaseUrlParameter = "baseUrl";

        public bool CanCreate(ModuleConfiguration configuration)
        {
            return configuration != null &&
                   configuration.ActivationMode == ModuleActivationMode.RemoteService &&
                   configuration.Parameters.ContainsKey(BaseUrlParameter);
        }

        public ValueTask<IModule> CreateAsync(ModuleConfiguration configuration, CancellationToken cancellationToken = default)
        {
            if (!CanCreate(configuration))
            {
                throw new NotSupportedException($"Module activation mode '{configuration?.ActivationMode}' is not supported by {nameof(RemoteServiceModuleFactory)}.");
            }

            var client = new RemoteServiceTransportClient(configuration.Parameters[BaseUrlParameter]);
            return CreateModuleAsync(configuration, client, cancellationToken);
        }

        internal static async ValueTask<IModule> CreateModuleAsync(ModuleConfiguration configuration, IModuleTransportClient client, CancellationToken cancellationToken)
        {
            IModule module = configuration.Kind switch
            {
                ModuleKind.Input => new TransportInputModule(client, configuration.ActivationMode),
                ModuleKind.Signal => new TransportSignalModule(client, configuration.ActivationMode),
                ModuleKind.Target => new TransportTargetModule(client, configuration.ActivationMode),
                ModuleKind.Constraint => new TransportConstraintModule(client, configuration.ActivationMode),
                ModuleKind.Execution => new TransportExecutionModule(client, configuration.ActivationMode),
                ModuleKind.MarketRule => new TransportMarketRuleModule(client, configuration.ActivationMode),
                ModuleKind.Analyzer => new TransportAnalyzerModule(client, configuration.ActivationMode),
                _ => throw new NotSupportedException($"Activation mode '{configuration.ActivationMode}' does not currently support module kind '{configuration.Kind}'.")
            };

            await module.Initialize(configuration, cancellationToken).ConfigureAwait(false);
            return module;
        }
    }
}
