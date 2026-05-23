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
    public sealed class OutOfProcessWorkerModuleFactory : IModuleFactory
    {
        public const string CommandParameter = "command";
        public const string ArgumentsParameter = "arguments";
        public const string WorkingDirectoryParameter = "workingDirectory";

        public bool CanCreate(ModuleConfiguration configuration)
        {
            return configuration != null &&
                   configuration.ActivationMode == ModuleActivationMode.OutOfProcessWorker &&
                   configuration.Parameters.ContainsKey(CommandParameter);
        }

        public ValueTask<IModule> CreateAsync(ModuleConfiguration configuration, CancellationToken cancellationToken = default)
        {
            if (!CanCreate(configuration))
            {
                throw new NotSupportedException($"Module activation mode '{configuration?.ActivationMode}' is not supported by {nameof(OutOfProcessWorkerModuleFactory)}.");
            }

            configuration.Parameters.TryGetValue(ArgumentsParameter, out var arguments);
            configuration.Parameters.TryGetValue(WorkingDirectoryParameter, out var workingDirectory);

            var client = new JsonLineProcessTransportClient(configuration.Parameters[CommandParameter], arguments, workingDirectory);
            return RemoteServiceModuleFactory.CreateModuleAsync(configuration, client, cancellationToken);
        }
    }
}
