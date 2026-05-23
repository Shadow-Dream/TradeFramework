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

using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Modules;
using QuantConnect.Orders;
using QuantConnect.Python;
using System.Threading;
using System.Threading.Tasks;

namespace QuantConnect.Algorithm.Framework.Execution
{
    /// <summary>
    /// Provides a base class for execution models
    /// </summary>
    public class ExecutionModel : BasePythonWrapper<ExecutionModel>, IExecutionModel, IModule
    {
        private readonly ModuleState _moduleState;

        /// <summary>
        /// If true, orders should be submitted asynchronously.
        /// </summary>
        protected bool Asynchronous { get; }

        public string Key => _moduleState.Key;
        public ModuleKind Kind => _moduleState.Kind;
        public ModuleActivationMode ActivationMode => _moduleState.ActivationMode;
        public string Version => _moduleState.Version;
        public ModuleHotSwapMode HotSwapMode => _moduleState.HotSwapMode;

        /// <summary>
        /// Initializes a new instance of the <see cref="ExecutionModel"/> class.
        /// </summary>
        /// <param name="asynchronous">If true, orders should be submitted asynchronously</param>
        public ExecutionModel(bool asynchronous = true)
        {
            _moduleState = new ModuleState(GetType(), ModuleKind.Execution, ModuleHotSwapMode.RequiresPause);
            Asynchronous = asynchronous;
        }

        /// <summary>
        /// Submit orders for the specified portfolio targets.
        /// This model is free to delay or spread out these orders as it sees fit
        /// </summary>
        /// <param name="algorithm">The algorithm instance</param>
        /// <param name="targets">The portfolio targets just emitted by the portfolio construction model.
        /// These are always just the new/updated targets and not a complete set of targets</param>
        public virtual void Execute(QCAlgorithm algorithm, IPortfolioTarget[] targets)
        {
            throw new System.NotImplementedException("Types deriving from 'ExecutionModel' must implement the 'void Execute(QCAlgorithm, IPortfolioTarget[]) method.");
        }

        /// <summary>
        /// Event fired each time the we add/remove securities from the data feed
        /// </summary>
        /// <param name="algorithm">The algorithm instance that experienced the change in securities</param>
        /// <param name="changes">The security additions and removals from the algorithm</param>
        public virtual void OnSecuritiesChanged(QCAlgorithm algorithm, SecurityChanges changes)
        {
        }

        /// <summary>
        /// New order event handler
        /// </summary>
        /// <param name="algorithm">The algorithm instance</param>
        /// <param name="orderEvent">Order event to process</param>
        public virtual void OnOrderEvent(QCAlgorithm algorithm, OrderEvent orderEvent)
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
