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
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Modules;

namespace QuantConnect.Algorithm.Modules
{
    /// <summary>
    /// Runtime contract for a typed alpha graph node.
    /// </summary>
    public interface IAlphaGraphNode : IModule
    {
        /// <summary>
        /// Evaluates this node for the current slice using named input port values.
        /// </summary>
        IReadOnlyDictionary<string, object> Evaluate(
            QCAlgorithm algorithm,
            Slice data,
            AlphaGraphNodeBinding binding,
            IReadOnlyDictionary<string, object> inputs);

        /// <summary>
        /// Notifies this node about security additions/removals.
        /// </summary>
        void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes);
    }
}
