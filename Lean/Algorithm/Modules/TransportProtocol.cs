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
using Newtonsoft.Json.Linq;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Securities;
using QuantConnect.Orders;
using QuantConnect.Data.Market;
using QuantConnect.Modules;

namespace QuantConnect.Algorithm.Modules
{
    internal static class TransportProtocol
    {
        public static JObject SerializeConfiguration(ModuleConfiguration configuration)
        {
            var parameters = new JObject();
            foreach (var (key, value) in configuration.Parameters)
            {
                parameters[key] = value;
            }

            return new JObject
            {
                ["key"] = configuration.Key,
                ["kind"] = configuration.Kind.ToString(),
                ["activationMode"] = configuration.ActivationMode.ToString(),
                ["entryPoint"] = configuration.EntryPoint,
                ["version"] = configuration.Version,
                ["hotSwapMode"] = configuration.HotSwapMode.ToString(),
                ["parameters"] = parameters
            };
        }

        public static JObject SerializeTime(QCAlgorithm algorithm)
        {
            return new JObject
            {
                ["timeUtc"] = algorithm.UtcTime
            };
        }

        public static JObject SerializeInsights(QCAlgorithm algorithm, IReadOnlyCollection<Insight> insights)
        {
            var array = new JArray();
            foreach (var insight in insights)
            {
                array.Add(new JObject
                {
                    ["symbol"] = SerializeSymbol(insight.Symbol),
                    ["direction"] = insight.Direction.ToString(),
                    ["periodDays"] = insight.Period.TotalDays,
                    ["sourceModel"] = insight.SourceModel ?? string.Empty
                });
            }

            var payload = SerializeTime(algorithm);
            payload["insights"] = array;
            return payload;
        }

        public static JObject SerializeSymbol(Symbol symbol)
        {
            return new JObject
            {
                ["value"] = symbol.Value,
                ["securityType"] = symbol.SecurityType.ToString(),
                ["market"] = symbol.ID.Market
            };
        }

        public static Symbol DeserializeSymbol(JToken token)
        {
            var value = (string)token["value"] ?? throw new InvalidOperationException("Symbol value is required.");
            var securityType = Enum.Parse<SecurityType>((string)token["securityType"], true);
            var market = (string)token["market"] ?? Market.USA;
            return Symbol.Create(value, securityType, market);
        }

        public static IEnumerable<InputRegistration> DeserializeInputs(JObject payload)
        {
            foreach (var item in payload["inputs"] as JArray ?? [])
            {
                yield return new InputRegistration(
                    DeserializeSymbol(item["symbol"]),
                    item["resolution"] != null ? Enum.Parse<Resolution>((string)item["resolution"], true) : null,
                    item["fillForward"]?.Value<bool>(),
                    item["leverage"]?.Value<decimal>() ?? 0m,
                    item["extendedMarketHours"]?.Value<bool>());
            }
        }

        public static IEnumerable<Insight> DeserializeInsights(JObject payload)
        {
            foreach (var item in payload["insights"] as JArray ?? [])
            {
                yield return Insight.Price(
                    DeserializeSymbol(item["symbol"]),
                    TimeSpan.FromDays(item["periodDays"]?.Value<double>() ?? 1),
                    Enum.Parse<InsightDirection>((string)item["direction"], true),
                    sourceModel: (string)item["sourceModel"]);
            }
        }

        public static IEnumerable<IPortfolioTarget> DeserializeTargets(QCAlgorithm algorithm, JObject payload)
        {
            foreach (var item in payload["targets"] as JArray ?? [])
            {
                var symbol = DeserializeSymbol(item["symbol"]);
                var tag = (string)item["tag"];

                if (item["quantity"] != null)
                {
                    yield return new PortfolioTarget(symbol, item["quantity"].Value<decimal>(), tag);
                    continue;
                }

                if (item["percent"] != null)
                {
                    var target = PortfolioTarget.Percent(algorithm, symbol, item["percent"].Value<decimal>(), tag: tag);
                    if (target != null)
                    {
                        yield return target;
                    }
                }
            }
        }

        public static JObject SerializeTargets(QCAlgorithm algorithm, IReadOnlyCollection<IPortfolioTarget> targets)
        {
            var array = new JArray();
            foreach (var target in targets)
            {
                array.Add(new JObject
                {
                    ["symbol"] = SerializeSymbol(target.Symbol),
                    ["quantity"] = target.Quantity,
                    ["tag"] = target.Tag ?? string.Empty
                });
            }

            var payload = SerializeTime(algorithm);
            payload["targets"] = array;
            return payload;
        }

        public static JObject SerializeOrder(Security security, Order order)
        {
            return new JObject
            {
                ["symbol"] = SerializeSymbol(security.Symbol),
                ["quantity"] = order.Quantity,
                ["type"] = order.Type.ToString(),
                ["direction"] = order.Direction.ToString(),
                ["tag"] = order.Tag ?? string.Empty
            };
        }

        public static IEnumerable<ExecutionInstruction> DeserializeExecutionInstructions(JObject payload)
        {
            foreach (var item in payload["orders"] as JArray ?? [])
            {
                yield return new ExecutionInstruction(
                    DeserializeSymbol(item["symbol"]),
                    item["quantity"]?.Value<decimal>() ?? 0m,
                    (string)item["tag"] ?? string.Empty);
            }
        }

        public static BrokerageDecision DeserializeBrokerageDecision(JObject payload)
        {
            return new BrokerageDecision(
                payload["allowed"]?.Value<bool>() ?? true,
                payload["leverage"]?.Value<decimal>() ?? 1m,
                payload["fee"]?.Value<decimal>() ?? 0m,
                payload["slippage"]?.Value<decimal>() ?? 0m,
                (string)payload["marker"] ?? string.Empty,
                (string)payload["message"] ?? string.Empty);
        }

        public static JObject SerializeObservations(IReadOnlyDictionary<ObservationKey, object> observations)
        {
            var payload = new JObject();
            foreach (var (key, value) in observations)
            {
                payload[key.ToString()] = value == null ? JValue.CreateNull() : JToken.FromObject(value);
            }
            return new JObject
            {
                ["observations"] = payload
            };
        }

        public static AnalyzerResult DeserializeAnalyzerResult(JObject payload)
        {
            var values = new Dictionary<string, object>(StringComparer.Ordinal);
            foreach (var property in (payload["values"] as JObject ?? new JObject()).Properties())
            {
                values[property.Name] = property.Value.Type switch
                {
                    JTokenType.Integer => property.Value.Value<long>(),
                    JTokenType.Float => property.Value.Value<decimal>(),
                    JTokenType.Boolean => property.Value.Value<bool>(),
                    JTokenType.String => property.Value.Value<string>(),
                    _ => property.Value.ToString()
                };
            }

            return new AnalyzerResult(values);
        }
    }

    internal sealed record ExecutionInstruction(Symbol Symbol, decimal Quantity, string Tag);

    internal sealed record BrokerageDecision(bool Allowed, decimal Leverage, decimal Fee, decimal Slippage, string Marker, string Message);
}
