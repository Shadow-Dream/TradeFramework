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
using Newtonsoft.Json.Linq;
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Logging;
using QuantConnect.Modules;
using QuantConnect.Orders;

namespace QuantConnect.Algorithm.Modules
{
    internal sealed class TransportExecutionModule : IExecutionModel, IModule, IDisposable
    {
        private readonly IModuleTransportClient _client;
        private readonly ModuleActivationMode _activationMode;
        private ModuleConfiguration _configuration;

        public TransportExecutionModule(IModuleTransportClient client, ModuleActivationMode activationMode)
        {
            _client = client ?? throw new ArgumentNullException(nameof(client));
            _activationMode = activationMode;
        }

        public string Key => _configuration?.Key ?? string.Empty;
        public ModuleKind Kind => ModuleKind.Execution;
        public ModuleActivationMode ActivationMode => _activationMode;
        public string Version => _configuration?.Version ?? "1.0.0";
        public ModuleHotSwapMode HotSwapMode => _configuration?.HotSwapMode ?? ModuleHotSwapMode.RequiresPause;

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

        public void Execute(QCAlgorithm algorithm, IPortfolioTarget[] targets)
        {
            var payload = _client.InvokeAsync("execute_targets", TransportProtocol.SerializeTargets(algorithm, targets)).GetAwaiter().GetResult();
            if (payload["marker"] != null)
            {
                Log.Trace((string)payload["marker"]);
            }

            foreach (var instruction in TransportProtocol.DeserializeExecutionInstructions(payload))
            {
                if (!algorithm.Securities.TryGetValue(instruction.Symbol, out var security))
                {
                    continue;
                }

                var target = new PortfolioTarget(instruction.Symbol, instruction.Quantity, instruction.Tag);
                var quantity = OrderSizing.GetUnorderedQuantity(algorithm, target, security, true);
                if (quantity != 0)
                {
                    algorithm.MarketOrder(instruction.Symbol, quantity, tag: instruction.Tag);
                }
            }
        }

        public void OnOrderEvent(QCAlgorithm algorithm, OrderEvent orderEvent)
        {
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
