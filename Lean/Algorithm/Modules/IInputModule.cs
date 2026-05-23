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

using System.Collections.Generic;
using QuantConnect.Data;
using QuantConnect.Modules;

namespace QuantConnect.Algorithm.Modules
{
    /// <summary>
    /// Describes a market input the strategy wants the engine to register.
    /// </summary>
    public sealed record InputRegistration(
        Symbol Symbol,
        Resolution? Resolution = null,
        bool? FillForward = null,
        decimal Leverage = 0m,
        bool? ExtendedMarketHours = null)
    {
        public static InputRegistration Create(
            Symbol symbol,
            Resolution? resolution = null,
            bool? fillForward = null,
            decimal leverage = 0m,
            bool? extendedMarketHours = null)
        {
            return new InputRegistration(symbol, resolution, fillForward, leverage, extendedMarketHours);
        }

        public static InputRegistration Equity(
            string ticker,
            Resolution? resolution = null,
            string market = Market.USA,
            bool? fillForward = null,
            decimal leverage = 0m,
            bool? extendedMarketHours = null)
        {
            return Create(Symbol.Create(ticker, SecurityType.Equity, market), resolution, fillForward, leverage, extendedMarketHours);
        }

        public static InputRegistration Forex(
            string ticker,
            Resolution? resolution = null,
            string market = Market.Oanda,
            bool? fillForward = null,
            decimal leverage = 0m,
            bool? extendedMarketHours = null)
        {
            return Create(Symbol.Create(ticker, SecurityType.Forex, market), resolution, fillForward, leverage, extendedMarketHours);
        }

        public static InputRegistration Crypto(
            string ticker,
            Resolution? resolution = null,
            string market = Market.Coinbase,
            bool? fillForward = null,
            decimal leverage = 0m,
            bool? extendedMarketHours = null)
        {
            return Create(Symbol.Create(ticker, SecurityType.Crypto, market), resolution, fillForward, leverage, extendedMarketHours);
        }
    }

    /// <summary>
    /// Input-stage module contract used to add and refresh registered market inputs at runtime.
    /// </summary>
    public interface IInputModule : IModule
    {
        IEnumerable<InputRegistration> CreateInputs(QCAlgorithm algorithm);
    }
}
