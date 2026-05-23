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
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;
using QuantConnect.Data;
using QuantConnect.Modules;

namespace QuantConnect.Algorithm.Modules
{
    /// <summary>
    /// Configurable built-in input module that turns per-instance JSON config into input registrations.
    /// </summary>
    public sealed class JsonInputModule : IInputModule
    {
        public const string ConfigParameter = "config";
        public const string SymbolsParameter = "symbols";

        private readonly ModuleState _state = new(typeof(JsonInputModule), ModuleKind.Input, ModuleHotSwapMode.Live);
        private IReadOnlyList<InputRegistration> _inputs = Array.Empty<InputRegistration>();

        public string Key => _state.Key;
        public ModuleKind Kind => _state.Kind;
        public ModuleActivationMode ActivationMode => _state.ActivationMode;
        public string Version => _state.Version;
        public ModuleHotSwapMode HotSwapMode => _state.HotSwapMode;

        public async ValueTask Initialize(ModuleConfiguration configuration, CancellationToken cancellationToken = default)
        {
            await _state.Initialize(configuration, cancellationToken).ConfigureAwait(false);
            _inputs = ParseInputs(configuration);
        }

        public ValueTask Pause(CancellationToken cancellationToken = default) => _state.Pause(cancellationToken);
        public ValueTask Resume(CancellationToken cancellationToken = default) => _state.Resume(cancellationToken);
        public ValueTask<ModuleSnapshot> CreateSnapshot(CancellationToken cancellationToken = default) => _state.CreateSnapshot(cancellationToken);
        public ValueTask RestoreSnapshot(ModuleSnapshot snapshot, CancellationToken cancellationToken = default) => _state.RestoreSnapshot(snapshot, cancellationToken);
        public ValueTask<ModuleHealthCheckResult> CheckHealth(CancellationToken cancellationToken = default) => _state.CheckHealth(cancellationToken);

        public IEnumerable<InputRegistration> CreateInputs(QCAlgorithm algorithm)
        {
            return _inputs;
        }

        private static IReadOnlyList<InputRegistration> ParseInputs(ModuleConfiguration configuration)
        {
            var config = ReadConfig(configuration);
            if (config == null)
            {
                return Array.Empty<InputRegistration>();
            }

            var defaults = new InputDefaults(config);
            var items = config["inputs"] as JArray ?? config["symbols"] as JArray ?? new JArray();
            return items.Select(item => ParseInput(item, defaults)).ToArray();
        }

        private static JObject ReadConfig(ModuleConfiguration configuration)
        {
            if (configuration?.Parameters == null)
            {
                return null;
            }

            if (configuration.Parameters.TryGetValue(ConfigParameter, out var configJson) && !string.IsNullOrWhiteSpace(configJson))
            {
                return JObject.Parse(configJson);
            }

            if (configuration.Parameters.TryGetValue(SymbolsParameter, out var symbolsJson) && !string.IsNullOrWhiteSpace(symbolsJson))
            {
                return new JObject
                {
                    ["symbols"] = JArray.Parse(symbolsJson)
                };
            }

            return null;
        }

        private static InputRegistration ParseInput(JToken item, InputDefaults defaults)
        {
            if (item.Type == JTokenType.String)
            {
                return new InputRegistration(
                    Symbol.Create(item.Value<string>(), defaults.SecurityType, defaults.Market),
                    defaults.Resolution,
                    defaults.FillForward,
                    defaults.Leverage,
                    defaults.ExtendedMarketHours);
            }

            if (item is not JObject input)
            {
                throw new InvalidOperationException($"JsonInputModule input item must be a string or object: {item}");
            }

            var symbolToken = input["symbol"] ?? input;
            var symbol = ParseSymbol(symbolToken, defaults);
            return new InputRegistration(
                symbol,
                ParseEnum<Resolution>(input["resolution"]) ?? defaults.Resolution,
                input["fillForward"]?.Value<bool>() ?? defaults.FillForward,
                input["leverage"]?.Value<decimal>() ?? defaults.Leverage,
                input["extendedMarketHours"]?.Value<bool>() ?? defaults.ExtendedMarketHours);
        }

        private static Symbol ParseSymbol(JToken token, InputDefaults defaults)
        {
            if (token.Type == JTokenType.String)
            {
                return Symbol.Create(token.Value<string>(), defaults.SecurityType, defaults.Market);
            }

            if (token is not JObject symbol)
            {
                throw new InvalidOperationException($"JsonInputModule symbol must be a string or object: {token}");
            }

            var value = (string)symbol["value"] ?? (string)symbol["ticker"] ?? (string)symbol["symbol"];
            if (string.IsNullOrWhiteSpace(value))
            {
                throw new InvalidOperationException("JsonInputModule symbol requires value, ticker, or symbol.");
            }

            var securityType = ParseEnum<SecurityType>(symbol["securityType"]) ?? defaults.SecurityType;
            var market = (string)symbol["market"] ?? defaults.Market;
            return Symbol.Create(value, securityType, market);
        }

        private static T? ParseEnum<T>(JToken token) where T : struct
        {
            return token == null || token.Type == JTokenType.Null
                ? null
                : Enum.Parse<T>((string)token, true);
        }

        private sealed class InputDefaults
        {
            public SecurityType SecurityType { get; }
            public string Market { get; }
            public Resolution? Resolution { get; }
            public bool? FillForward { get; }
            public decimal Leverage { get; }
            public bool? ExtendedMarketHours { get; }

            public InputDefaults(JObject config)
            {
                SecurityType = ParseEnum<SecurityType>(config["securityType"]) ?? SecurityType.Equity;
                Market = (string)config["market"] ?? DefaultMarket(SecurityType);
                Resolution = ParseEnum<Resolution>(config["resolution"]);
                FillForward = config["fillForward"]?.Value<bool>();
                Leverage = config["leverage"]?.Value<decimal>() ?? 0m;
                ExtendedMarketHours = config["extendedMarketHours"]?.Value<bool>();
            }

            private static string DefaultMarket(SecurityType securityType)
            {
                return securityType switch
                {
                    SecurityType.Forex => QuantConnect.Market.Oanda,
                    SecurityType.Crypto => QuantConnect.Market.Coinbase,
                    _ => QuantConnect.Market.USA
                };
            }
        }
    }
}
