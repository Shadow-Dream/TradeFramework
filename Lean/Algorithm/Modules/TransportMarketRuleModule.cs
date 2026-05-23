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
using QuantConnect.Benchmarks;
using QuantConnect.Brokerages;
using QuantConnect.Data.Market;
using QuantConnect.Interfaces;
using QuantConnect.Logging;
using QuantConnect.Modules;
using QuantConnect.Orders;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Orders.Slippage;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.Modules
{
    internal sealed class TransportMarketRuleModule : IBrokerageModel, IModule, IDisposable
    {
        private readonly IModuleTransportClient _client;
        private readonly ModuleActivationMode _activationMode;
        private readonly DefaultBrokerageModel _baseline = new();
        private ModuleConfiguration _configuration;

        public TransportMarketRuleModule(IModuleTransportClient client, ModuleActivationMode activationMode)
        {
            _client = client ?? throw new ArgumentNullException(nameof(client));
            _activationMode = activationMode;
        }

        public AccountType AccountType => AccountType.Margin;
        public decimal RequiredFreeBuyingPowerPercent => _baseline.RequiredFreeBuyingPowerPercent;
        public IReadOnlyDictionary<SecurityType, string> DefaultMarkets => _baseline.DefaultMarkets;
        public string Key => _configuration?.Key ?? string.Empty;
        public ModuleKind Kind => ModuleKind.MarketRule;
        public ModuleActivationMode ActivationMode => _activationMode;
        public string Version => _configuration?.Version ?? "1.0.0";
        public ModuleHotSwapMode HotSwapMode => _configuration?.HotSwapMode ?? ModuleHotSwapMode.RequiresFlatNoOrders;

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

        public bool CanSubmitOrder(Security security, Order order, out BrokerageMessageEvent message)
        {
            var decision = InvokeMarketRule("can_submit_order", security, order);
            message = decision.Allowed || string.IsNullOrWhiteSpace(decision.Message)
                ? null
                : new BrokerageMessageEvent(BrokerageMessageType.Warning, "transport-market-rule", decision.Message);
            return decision.Allowed;
        }

        public bool CanUpdateOrder(Security security, Order order, UpdateOrderRequest request, out BrokerageMessageEvent message)
        {
            return _baseline.CanUpdateOrder(security, order, request, out message);
        }

        public bool CanExecuteOrder(Security security, Order order)
        {
            return InvokeMarketRule("can_execute_order", security, order).Allowed;
        }

        public void ApplySplit(List<OrderTicket> tickets, Split split)
        {
            _baseline.ApplySplit(tickets, split);
        }

        public decimal GetLeverage(Security security)
        {
            return InvokeMarketRule("describe_market_rule", security).Leverage;
        }

        public IBenchmark GetBenchmark(SecurityManager securities)
        {
            return _baseline.GetBenchmark(securities);
        }

        public IFillModel GetFillModel(Security security)
        {
            return _baseline.GetFillModel(security);
        }

        public IFeeModel GetFeeModel(Security security)
        {
            return new TransportFeeModel(this, security);
        }

        public ISlippageModel GetSlippageModel(Security security)
        {
            return new TransportSlippageModel(this, security);
        }

        public ISettlementModel GetSettlementModel(Security security)
        {
            return _baseline.GetSettlementModel(security);
        }

        public IMarginInterestRateModel GetMarginInterestRateModel(Security security)
        {
            return _baseline.GetMarginInterestRateModel(security);
        }

        [Obsolete]
        public ISettlementModel GetSettlementModel(Security security, AccountType accountType)
        {
            return _baseline.GetSettlementModel(security, accountType);
        }

        public IBuyingPowerModel GetBuyingPowerModel(Security security)
        {
            return _baseline.GetBuyingPowerModel(security);
        }

        [Obsolete]
        public IBuyingPowerModel GetBuyingPowerModel(Security security, AccountType accountType)
        {
            return _baseline.GetBuyingPowerModel(security, accountType);
        }

        public IShortableProvider GetShortableProvider(Security security)
        {
            return _baseline.GetShortableProvider(security);
        }

        public void Dispose()
        {
            _client.Dispose();
        }

        private BrokerageDecision InvokeMarketRule(string command, Security security, Order order = null)
        {
            var payload = new JObject
            {
                ["moduleKey"] = Key,
                ["security"] = TransportProtocol.SerializeSymbol(security.Symbol)
            };

            if (order != null)
            {
                payload["order"] = TransportProtocol.SerializeOrder(security, order);
            }

            var response = _client.InvokeAsync(command, payload).GetAwaiter().GetResult();
            var decision = TransportProtocol.DeserializeBrokerageDecision(response);
            if (!string.IsNullOrWhiteSpace(decision.Marker))
            {
                Log.Trace(decision.Marker);
            }
            return decision;
        }

        private sealed class TransportFeeModel : IFeeModel
        {
            private readonly TransportMarketRuleModule _owner;
            private readonly Security _security;

            public TransportFeeModel(TransportMarketRuleModule owner, Security security)
            {
                _owner = owner;
                _security = security;
            }

            public OrderFee GetOrderFee(OrderFeeParameters parameters)
            {
                var decision = _owner.InvokeMarketRule("describe_market_rule", _security, parameters.Order);
                return new OrderFee(new CashAmount(decision.Fee, "USD"));
            }
        }

        private sealed class TransportSlippageModel : ISlippageModel
        {
            private readonly TransportMarketRuleModule _owner;
            private readonly Security _security;

            public TransportSlippageModel(TransportMarketRuleModule owner, Security security)
            {
                _owner = owner;
                _security = security;
            }

            public decimal GetSlippageApproximation(Security asset, Order order)
            {
                return _owner.InvokeMarketRule("describe_market_rule", _security, order).Slippage;
            }
        }
    }
}
