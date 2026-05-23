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
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Modules;
using System.Threading;
using System.Threading.Tasks;

namespace QuantConnect.Algorithm.Framework.Risk
{
    /// <summary>
    /// Provides a base class for risk management models
    /// </summary>
    public class RiskManagementModel : IRiskManagementModel, IModule
    {
        private readonly ModuleState _moduleState;

        public string Key => _moduleState.Key;
        public ModuleKind Kind => _moduleState.Kind;
        public ModuleActivationMode ActivationMode => _moduleState.ActivationMode;
        public string Version => _moduleState.Version;
        public ModuleHotSwapMode HotSwapMode => _moduleState.HotSwapMode;

        protected RiskManagementModel()
        {
            _moduleState = new ModuleState(GetType(), ModuleKind.Constraint, ModuleHotSwapMode.Live);
        }

        /// <summary>
        /// Manages the algorithm's risk at each time step
        /// </summary>
        /// <param name="algorithm">The algorithm instance</param>
        /// <param name="targets">The current portfolio targets to be assessed for risk</param>
        public virtual IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm algorithm, IPortfolioTarget[] targets)
        {
            throw new System.NotImplementedException("Types deriving from 'RiskManagementModel' must implement the 'IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm, IPortfolioTarget[]) method.");
        }

        /// <summary>
        /// Event fired each time the we add/remove securities from the data feed
        /// </summary>
        /// <param name="algorithm">The algorithm instance that experienced the change in securities</param>
        /// <param name="changes">The security additions and removals from the algorithm</param>
        public virtual void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes)
        {
        }

        public virtual ValueTask Initialize(ModuleConfiguration configuration, CancellationToken cancellationToken = default)
        {
            return _moduleState.Initialize(configuration, cancellationToken);
        }

        public virtual ValueTask Pause(CancellationToken cancellationToken = default)
        {
            return _moduleState.Pause(cancellationToken);
        }

        public virtual ValueTask Resume(CancellationToken cancellationToken = default)
        {
            return _moduleState.Resume(cancellationToken);
        }

        public virtual ValueTask<ModuleSnapshot> CreateSnapshot(CancellationToken cancellationToken = default)
        {
            return _moduleState.CreateSnapshot(cancellationToken);
        }

        public virtual ValueTask RestoreSnapshot(ModuleSnapshot snapshot, CancellationToken cancellationToken = default)
        {
            return _moduleState.RestoreSnapshot(snapshot, cancellationToken);
        }

        public virtual ValueTask<ModuleHealthCheckResult> CheckHealth(CancellationToken cancellationToken = default)
        {
            return _moduleState.CheckHealth(cancellationToken);
        }
    }
}
