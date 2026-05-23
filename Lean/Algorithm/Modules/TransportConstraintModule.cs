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
using Newtonsoft.Json.Linq;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Logging;
using QuantConnect.Modules;

namespace QuantConnect.Algorithm.Modules
{
    internal sealed class TransportConstraintModule : IRiskManagementModel, IModule, IDisposable
    {
        private readonly IModuleTransportClient _client;
        private readonly ModuleActivationMode _activationMode;
        private ModuleConfiguration _configuration;

        public TransportConstraintModule(IModuleTransportClient client, ModuleActivationMode activationMode)
        {
            _client = client ?? throw new ArgumentNullException(nameof(client));
            _activationMode = activationMode;
        }

        public string Key => _configuration?.Key ?? string.Empty;
        public ModuleKind Kind => ModuleKind.Constraint;
        public ModuleActivationMode ActivationMode => _activationMode;
        public string Version => _configuration?.Version ?? "1.0.0";
        public ModuleHotSwapMode HotSwapMode => _configuration?.HotSwapMode ?? ModuleHotSwapMode.Live;

        public async ValueTask Initialize(ModuleConfiguration configuration, CancellationToken cancellationToken = default)
        {
            _configuration = configuration ?? throw new ArgumentNullException(nameof(configuration));
            await _client.InvokeAsync("initialize", new JObject
            {
                ["configuration"] = TransportProtocol.SerializeConfiguration(configuration)
            }, cancellationToken).ConfigureAwait(false);
        }

        public async ValueTask Pause(CancellationToken cancellationToken = default)
        {
            await _client.InvokeAsync("pause", cancellationToken: cancellationToken).ConfigureAwait(false);
        }

        public async ValueTask Resume(CancellationToken cancellationToken = default)
        {
            await _client.InvokeAsync("resume", cancellationToken: cancellationToken).ConfigureAwait(false);
        }

        public async ValueTask<ModuleSnapshot> CreateSnapshot(CancellationToken cancellationToken = default)
        {
            var payload = await _client.InvokeAsync("snapshot", cancellationToken: cancellationToken).ConfigureAwait(false);
            return new ModuleSnapshot(Key, Version, Array.Empty<byte>(), (string)payload["contentType"] ?? "application/x.quantconnect.empty-snapshot");
        }

        public async ValueTask RestoreSnapshot(ModuleSnapshot snapshot, CancellationToken cancellationToken = default)
        {
            await _client.InvokeAsync("restore", new JObject
            {
                ["moduleKey"] = snapshot.ModuleKey
            }, cancellationToken).ConfigureAwait(false);
        }

        public async ValueTask<ModuleHealthCheckResult> CheckHealth(CancellationToken cancellationToken = default)
        {
            var payload = await _client.InvokeAsync("health", cancellationToken: cancellationToken).ConfigureAwait(false);
            return new ModuleHealthCheckResult(Enum.Parse<ModuleHealthStatus>((string)payload["status"], true));
        }

        public IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm algorithm, IPortfolioTarget[] targets)
        {
            var request = TransportProtocol.SerializeTargets(algorithm, targets);
            request["moduleKey"] = Key;
            var payload = _client.InvokeAsync("manage_risk", request).GetAwaiter().GetResult();
            if (payload["marker"] != null)
            {
                Log.Trace((string)payload["marker"]);
            }
            return TransportProtocol.DeserializeTargets(algorithm, payload);
        }

        public void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes)
        {
        }

        public void Dispose()
        {
            _client.Dispose();
        }
    }
}
