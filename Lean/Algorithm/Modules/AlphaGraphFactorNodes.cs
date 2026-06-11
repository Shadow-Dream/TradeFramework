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
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Modules;

namespace QuantConnect.Algorithm.Modules
{
    public abstract class AlphaGraphFactorNode : IAlphaGraphNode
    {
        private readonly ModuleState _state;

        protected AlphaGraphFactorNode()
        {
            _state = new ModuleState(GetType(), ModuleKind.Signal, ModuleHotSwapMode.Live);
        }

        public string Key => _state.Key;
        public ModuleKind Kind => _state.Kind;
        public ModuleActivationMode ActivationMode => _state.ActivationMode;
        public string Version => _state.Version;
        public ModuleHotSwapMode HotSwapMode => _state.HotSwapMode;

        public ValueTask Initialize(ModuleConfiguration configuration, CancellationToken cancellationToken = default) => _state.Initialize(configuration, cancellationToken);
        public ValueTask Pause(CancellationToken cancellationToken = default) => _state.Pause(cancellationToken);
        public ValueTask Resume(CancellationToken cancellationToken = default) => _state.Resume(cancellationToken);
        public ValueTask<ModuleSnapshot> CreateSnapshot(CancellationToken cancellationToken = default) => _state.CreateSnapshot(cancellationToken);
        public ValueTask RestoreSnapshot(ModuleSnapshot snapshot, CancellationToken cancellationToken = default) => _state.RestoreSnapshot(snapshot, cancellationToken);
        public ValueTask<ModuleHealthCheckResult> CheckHealth(CancellationToken cancellationToken = default) => _state.CheckHealth(cancellationToken);

        public abstract IReadOnlyDictionary<string, object> Evaluate(
            QCAlgorithm algorithm,
            Slice data,
            AlphaGraphNodeBinding binding,
            IReadOnlyDictionary<string, object> inputs);

        public virtual void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes)
        {
        }

        protected static int ConfigInt(AlphaGraphNodeBinding binding, string name, int defaultValue)
        {
            return binding.Config.TryGetValue(name, out var value) && int.TryParse(value, out var parsed)
                ? parsed
                : defaultValue;
        }

        protected static decimal ConfigDecimal(AlphaGraphNodeBinding binding, string name, decimal defaultValue)
        {
            return binding.Config.TryGetValue(name, out var value) && decimal.TryParse(value, out var parsed)
                ? parsed
                : defaultValue;
        }

        protected static string ConfigString(AlphaGraphNodeBinding binding, string name, string defaultValue = "")
        {
            return binding.Config.TryGetValue(name, out var value) && !string.IsNullOrWhiteSpace(value)
                ? value
                : defaultValue;
        }

        protected static decimal? InputDecimal(IReadOnlyDictionary<string, object> inputs, string name)
        {
            if (!inputs.TryGetValue(name, out var value) || value == null)
            {
                return null;
            }

            return Convert.ToDecimal(value);
        }

        protected static IReadOnlyDictionary<string, object> Output(string name, object value)
        {
            return new Dictionary<string, object> { [name] = value };
        }
    }

    public sealed class PriceSourceNode : AlphaGraphFactorNode
    {
        public override IReadOnlyDictionary<string, object> Evaluate(
            QCAlgorithm algorithm,
            Slice data,
            AlphaGraphNodeBinding binding,
            IReadOnlyDictionary<string, object> inputs)
        {
            var symbol = ResolveSymbol(algorithm, binding);
            if (symbol == null)
            {
                return new Dictionary<string, object>();
            }

            var result = new Dictionary<string, object>();
            var securityPrice = algorithm.Securities.TryGetValue(symbol, out var security) ? security.Price : 0m;
            result["price"] = securityPrice;
            result["close"] = securityPrice;

            if (data.Bars.TryGetValue(symbol, out var bar))
            {
                result["open"] = bar.Open;
                result["high"] = bar.High;
                result["low"] = bar.Low;
                result["close"] = bar.Close;
                result["price"] = SelectPrice(bar, ConfigString(binding, "priceField", "close"));
                result["volume"] = bar.Volume;
            }

            return result;
        }

        private static Symbol ResolveSymbol(QCAlgorithm algorithm, AlphaGraphNodeBinding binding)
        {
            var configured = ConfigString(binding, "symbol");
            if (!string.IsNullOrWhiteSpace(configured))
            {
                return algorithm.Securities.Keys.FirstOrDefault(symbol =>
                    string.Equals(symbol.Value, configured, StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(symbol.ID.Symbol, configured, StringComparison.OrdinalIgnoreCase));
            }

            return algorithm.Securities.Keys.FirstOrDefault();
        }

        private static decimal SelectPrice(TradeBar bar, string field)
        {
            switch ((field ?? "close").ToLowerInvariant())
            {
                case "open": return bar.Open;
                case "high": return bar.High;
                case "low": return bar.Low;
                default: return bar.Close;
            }
        }
    }

    public abstract class WindowFactorNode : AlphaGraphFactorNode
    {
        protected readonly Queue<decimal> Window = new();
        protected decimal Total;

        protected void Push(decimal value, int period)
        {
            Window.Enqueue(value);
            Total += value;
            while (Window.Count > period)
            {
                Total -= Window.Dequeue();
            }
        }
    }

    public sealed class SimpleMovingAverageNode : WindowFactorNode
    {
        public override IReadOnlyDictionary<string, object> Evaluate(QCAlgorithm algorithm, Slice data, AlphaGraphNodeBinding binding, IReadOnlyDictionary<string, object> inputs)
        {
            var value = InputDecimal(inputs, "value");
            if (value == null)
            {
                return Output("sma", null);
            }

            var period = Math.Max(1, ConfigInt(binding, "period", 20));
            Push(value.Value, period);
            return Output("sma", Window.Count == period ? Total / period : null);
        }
    }

    public sealed class ExponentialMovingAverageNode : AlphaGraphFactorNode
    {
        private decimal? _ema;

        public override IReadOnlyDictionary<string, object> Evaluate(QCAlgorithm algorithm, Slice data, AlphaGraphNodeBinding binding, IReadOnlyDictionary<string, object> inputs)
        {
            var value = InputDecimal(inputs, "value");
            if (value == null)
            {
                return Output("ema", null);
            }

            var period = Math.Max(1, ConfigInt(binding, "period", 20));
            var alpha = 2m / (period + 1m);
            _ema = _ema == null ? value.Value : value.Value * alpha + _ema.Value * (1m - alpha);
            return Output("ema", _ema.Value);
        }
    }

    public sealed class WeightedMovingAverageNode : WindowFactorNode
    {
        public override IReadOnlyDictionary<string, object> Evaluate(QCAlgorithm algorithm, Slice data, AlphaGraphNodeBinding binding, IReadOnlyDictionary<string, object> inputs)
        {
            var value = InputDecimal(inputs, "value");
            if (value == null)
            {
                return Output("wma", null);
            }

            var period = Math.Max(1, ConfigInt(binding, "period", 20));
            Push(value.Value, period);
            if (Window.Count < period)
            {
                return Output("wma", null);
            }

            var values = Window.ToArray();
            var weighted = 0m;
            var divisor = 0m;
            for (var i = 0; i < values.Length; i++)
            {
                var weight = i + 1;
                weighted += values[i] * weight;
                divisor += weight;
            }

            return Output("wma", weighted / divisor);
        }
    }

    public sealed class VolumeWeightedMovingAverageNode : AlphaGraphFactorNode
    {
        private readonly Queue<decimal> _priceVolume = new();
        private readonly Queue<decimal> _volume = new();
        private decimal _priceVolumeTotal;
        private decimal _volumeTotal;

        public override IReadOnlyDictionary<string, object> Evaluate(QCAlgorithm algorithm, Slice data, AlphaGraphNodeBinding binding, IReadOnlyDictionary<string, object> inputs)
        {
            var price = InputDecimal(inputs, "price");
            var volume = InputDecimal(inputs, "volume");
            if (price == null || volume == null)
            {
                return Output("vwma", null);
            }

            var period = Math.Max(1, ConfigInt(binding, "period", 20));
            _priceVolume.Enqueue(price.Value * volume.Value);
            _volume.Enqueue(volume.Value);
            _priceVolumeTotal += price.Value * volume.Value;
            _volumeTotal += volume.Value;
            while (_volume.Count > period)
            {
                _priceVolumeTotal -= _priceVolume.Dequeue();
                _volumeTotal -= _volume.Dequeue();
            }

            return Output("vwma", _volume.Count == period && _volumeTotal != 0m ? _priceVolumeTotal / _volumeTotal : null);
        }
    }

    public sealed class RelativeStrengthIndexNode : AlphaGraphFactorNode
    {
        private decimal? _previous;
        private decimal _averageGain;
        private decimal _averageLoss;
        private int _samples;

        public override IReadOnlyDictionary<string, object> Evaluate(QCAlgorithm algorithm, Slice data, AlphaGraphNodeBinding binding, IReadOnlyDictionary<string, object> inputs)
        {
            var price = InputDecimal(inputs, "price");
            if (price == null)
            {
                return Output("rsi", null);
            }

            var period = Math.Max(1, ConfigInt(binding, "period", 14));
            if (_previous == null)
            {
                _previous = price.Value;
                return Output("rsi", null);
            }

            var delta = price.Value - _previous.Value;
            var gain = Math.Max(delta, 0m);
            var loss = Math.Max(-delta, 0m);
            _previous = price.Value;
            _samples++;
            if (_samples <= period)
            {
                _averageGain += gain;
                _averageLoss += loss;
                if (_samples < period)
                {
                    return Output("rsi", null);
                }

                _averageGain /= period;
                _averageLoss /= period;
            }
            else
            {
                _averageGain = (_averageGain * (period - 1) + gain) / period;
                _averageLoss = (_averageLoss * (period - 1) + loss) / period;
            }

            if (_averageLoss == 0m)
            {
                return Output("rsi", 100m);
            }

            var rs = _averageGain / _averageLoss;
            return Output("rsi", 100m - 100m / (1m + rs));
        }
    }

    public sealed class MacdNode : AlphaGraphFactorNode
    {
        private readonly EmaTracker _fast = new();
        private readonly EmaTracker _slow = new();
        private readonly EmaTracker _signal = new();

        public override IReadOnlyDictionary<string, object> Evaluate(QCAlgorithm algorithm, Slice data, AlphaGraphNodeBinding binding, IReadOnlyDictionary<string, object> inputs)
        {
            var price = InputDecimal(inputs, "price");
            if (price == null)
            {
                return new Dictionary<string, object> { ["macd"] = null, ["signal"] = null, ["histogram"] = null };
            }

            var fast = _fast.Update(price.Value, Math.Max(1, ConfigInt(binding, "fastPeriod", 12)));
            var slow = _slow.Update(price.Value, Math.Max(1, ConfigInt(binding, "slowPeriod", 26)));
            var macd = fast - slow;
            var signal = _signal.Update(macd, Math.Max(1, ConfigInt(binding, "signalPeriod", 9)));
            return new Dictionary<string, object>
            {
                ["macd"] = macd,
                ["signal"] = signal,
                ["histogram"] = macd - signal
            };
        }

        private sealed class EmaTracker
        {
            private decimal? _value;

            public decimal Update(decimal value, int period)
            {
                var alpha = 2m / (period + 1m);
                _value = _value == null ? value : value * alpha + _value.Value * (1m - alpha);
                return _value.Value;
            }
        }
    }

    public sealed class BollingerBandsNode : WindowFactorNode
    {
        public override IReadOnlyDictionary<string, object> Evaluate(QCAlgorithm algorithm, Slice data, AlphaGraphNodeBinding binding, IReadOnlyDictionary<string, object> inputs)
        {
            var price = InputDecimal(inputs, "price");
            if (price == null)
            {
                return EmptyBands();
            }

            var period = Math.Max(1, ConfigInt(binding, "period", 20));
            var k = ConfigDecimal(binding, "k", 2m);
            Push(price.Value, period);
            if (Window.Count < period)
            {
                return EmptyBands();
            }

            var middle = Total / period;
            var variance = Window.Sum(value => (value - middle) * (value - middle)) / period;
            var deviation = (decimal)Math.Sqrt((double)variance);
            var upper = middle + k * deviation;
            var lower = middle - k * deviation;
            return new Dictionary<string, object>
            {
                ["middle"] = middle,
                ["upper"] = upper,
                ["lower"] = lower,
                ["bandwidth"] = middle != 0m ? (upper - lower) / middle : null,
                ["percentB"] = upper != lower ? (price.Value - lower) / (upper - lower) : null
            };
        }

        private static IReadOnlyDictionary<string, object> EmptyBands()
        {
            return new Dictionary<string, object>
            {
                ["middle"] = null,
                ["upper"] = null,
                ["lower"] = null,
                ["bandwidth"] = null,
                ["percentB"] = null
            };
        }
    }

    public sealed class AverageTrueRangeNode : WindowFactorNode
    {
        private decimal? _previousClose;

        public override IReadOnlyDictionary<string, object> Evaluate(QCAlgorithm algorithm, Slice data, AlphaGraphNodeBinding binding, IReadOnlyDictionary<string, object> inputs)
        {
            var high = InputDecimal(inputs, "high");
            var low = InputDecimal(inputs, "low");
            var close = InputDecimal(inputs, "close");
            if (high == null || low == null || close == null)
            {
                return Output("atr", null);
            }

            var range = high.Value - low.Value;
            if (_previousClose != null)
            {
                range = Math.Max(range, Math.Max(Math.Abs(high.Value - _previousClose.Value), Math.Abs(low.Value - _previousClose.Value)));
            }
            _previousClose = close.Value;

            var period = Math.Max(1, ConfigInt(binding, "period", 14));
            Push(range, period);
            return Output("atr", Window.Count == period ? Total / period : null);
        }
    }

    public sealed class StochasticNode : AlphaGraphFactorNode
    {
        private readonly Queue<decimal> _highs = new();
        private readonly Queue<decimal> _lows = new();
        private readonly Queue<decimal> _kValues = new();

        public override IReadOnlyDictionary<string, object> Evaluate(QCAlgorithm algorithm, Slice data, AlphaGraphNodeBinding binding, IReadOnlyDictionary<string, object> inputs)
        {
            var high = InputDecimal(inputs, "high");
            var low = InputDecimal(inputs, "low");
            var close = InputDecimal(inputs, "close");
            if (high == null || low == null || close == null)
            {
                return new Dictionary<string, object> { ["k"] = null, ["d"] = null };
            }

            var period = Math.Max(1, ConfigInt(binding, "period", 14));
            var dPeriod = Math.Max(1, ConfigInt(binding, "dPeriod", 3));
            Push(_highs, high.Value, period);
            Push(_lows, low.Value, period);
            if (_highs.Count < period)
            {
                return new Dictionary<string, object> { ["k"] = null, ["d"] = null };
            }

            var highest = _highs.Max();
            var lowest = _lows.Min();
            var k = highest != lowest ? 100m * (close.Value - lowest) / (highest - lowest) : 0m;
            Push(_kValues, k, dPeriod);
            var d = _kValues.Count == dPeriod ? _kValues.Average() : (decimal?)null;
            return new Dictionary<string, object> { ["k"] = k, ["d"] = d };
        }

        private static void Push(Queue<decimal> queue, decimal value, int period)
        {
            queue.Enqueue(value);
            while (queue.Count > period)
            {
                queue.Dequeue();
            }
        }
    }

    public sealed class OnBalanceVolumeNode : AlphaGraphFactorNode
    {
        private decimal? _previousClose;
        private decimal _obv;

        public override IReadOnlyDictionary<string, object> Evaluate(QCAlgorithm algorithm, Slice data, AlphaGraphNodeBinding binding, IReadOnlyDictionary<string, object> inputs)
        {
            var close = InputDecimal(inputs, "close");
            var volume = InputDecimal(inputs, "volume");
            if (close == null || volume == null)
            {
                return Output("obv", null);
            }

            if (_previousClose != null)
            {
                if (close.Value > _previousClose.Value)
                {
                    _obv += volume.Value;
                }
                else if (close.Value < _previousClose.Value)
                {
                    _obv -= volume.Value;
                }
            }

            _previousClose = close.Value;
            return Output("obv", _obv);
        }
    }

    public sealed class RateOfChangeNode : AlphaGraphFactorNode
    {
        private readonly Queue<decimal> _values = new();

        public override IReadOnlyDictionary<string, object> Evaluate(QCAlgorithm algorithm, Slice data, AlphaGraphNodeBinding binding, IReadOnlyDictionary<string, object> inputs)
        {
            var price = InputDecimal(inputs, "price");
            if (price == null)
            {
                return Output("roc", null);
            }

            var period = Math.Max(1, ConfigInt(binding, "period", 12));
            _values.Enqueue(price.Value);
            if (_values.Count <= period)
            {
                return Output("roc", null);
            }

            var previous = _values.Dequeue();
            return Output("roc", previous != 0m ? price.Value / previous - 1m : null);
        }
    }

    public sealed class CrossOverGateNode : AlphaGraphFactorNode
    {
        private int _previous;

        public override IReadOnlyDictionary<string, object> Evaluate(QCAlgorithm algorithm, Slice data, AlphaGraphNodeBinding binding, IReadOnlyDictionary<string, object> inputs)
        {
            var fast = InputDecimal(inputs, "fast");
            var slow = InputDecimal(inputs, "slow");
            if (fast == null || slow == null)
            {
                return Output("direction", null);
            }

            var current = fast.Value > slow.Value ? 1 : fast.Value < slow.Value ? -1 : 0;
            var direction = current > 0 && _previous <= 0
                ? "rise"
                : current < 0 && _previous >= 0
                    ? "fall"
                    : "flat";
            if (current != 0)
            {
                _previous = current;
            }

            return Output("direction", direction);
        }
    }

    public sealed class DirectionToPriceInsightNode : AlphaGraphFactorNode
    {
        public override IReadOnlyDictionary<string, object> Evaluate(QCAlgorithm algorithm, Slice data, AlphaGraphNodeBinding binding, IReadOnlyDictionary<string, object> inputs)
        {
            if (!inputs.TryGetValue("direction", out var value) || value == null)
            {
                return Output("insights", Array.Empty<Insight>());
            }

            var direction = Convert.ToString(value)?.ToLowerInvariant();
            var symbol = ResolveSymbol(algorithm, binding);
            if (symbol == null)
            {
                return Output("insights", Array.Empty<Insight>());
            }

            var insightDirection = direction switch
            {
                "rise" or "up" or "long" or "buy" => InsightDirection.Up,
                "fall" or "down" or "short" or "sell" => InsightDirection.Down,
                "flat" or "exit" => InsightDirection.Flat,
                _ => (InsightDirection?)null
            };
            if (insightDirection == null)
            {
                return Output("insights", Array.Empty<Insight>());
            }

            var period = TimeSpan.FromDays(Math.Max(1, ConfigInt(binding, "periodDays", 1)));
            return Output("insights", new[] { Insight.Price(symbol, period, insightDirection.Value, sourceModel: binding.InstanceId) });
        }

        private static Symbol ResolveSymbol(QCAlgorithm algorithm, AlphaGraphNodeBinding binding)
        {
            var configured = ConfigString(binding, "symbol");
            if (!string.IsNullOrWhiteSpace(configured))
            {
                return algorithm.Securities.Keys.FirstOrDefault(symbol =>
                    string.Equals(symbol.Value, configured, StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(symbol.ID.Symbol, configured, StringComparison.OrdinalIgnoreCase));
            }

            return algorithm.Securities.Keys.FirstOrDefault();
        }
    }
}
