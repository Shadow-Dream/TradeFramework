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
using System.Linq;

namespace QuantConnect.Algorithm.Framework.Portfolio
{
    /// <summary>
    /// Resolves conflicting intents per symbol using priority and source order.
    /// </summary>
    public sealed class PriorityPortfolioIntentMerger : IPortfolioIntentMerger
    {
        public static readonly PriorityPortfolioIntentMerger Instance = new();

        public IEnumerable<IPortfolioTarget> Merge(QCAlgorithm algorithm, IEnumerable<PortfolioTargetIntent> intents)
        {
            return intents
                .Select((intent, index) => new { intent, index })
                .GroupBy(x => x.intent.Symbol)
                .Select(group => group
                    .OrderBy(x => x.intent.Priority)
                    .ThenBy(x => x.index)
                    .Last().intent)
                .Select(intent => (IPortfolioTarget)new PortfolioTarget(intent.Symbol, intent.Quantity, intent.Tag));
        }
    }
}
