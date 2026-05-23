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
using QuantConnect.Data.UniverseSelection;
using QuantConnect.Interfaces;
using QuantConnect.Modules;
using QuantConnect.Python;
using System.Threading;
using System.Threading.Tasks;

namespace QuantConnect.Algorithm.Framework.Selection
{
    /// <summary>
    /// Provides a base class for universe selection models.
    /// </summary>
    public class UniverseSelectionModel : BasePythonWrapper<UniverseSelectionModel>, IUniverseSelectionModel, IModule
    {
        private readonly ModuleState _moduleState;

        public string Key => _moduleState.Key;
        public ModuleKind Kind => _moduleState.Kind;
        public ModuleActivationMode ActivationMode => _moduleState.ActivationMode;
        public string Version => _moduleState.Version;
        public ModuleHotSwapMode HotSwapMode => _moduleState.HotSwapMode;

        /// <summary>
        /// Initializes a new instance of the <see cref="UniverseSelectionModel"/> class.
        /// </summary>
        public UniverseSelectionModel()
        {
            _moduleState = new ModuleState(GetType(), ModuleKind.Universe, ModuleHotSwapMode.Live);
        }

        /// <summary>
        /// Gets the next time the framework should invoke the `CreateUniverses` method to refresh the set of universes.
        /// </summary>
        public virtual DateTime GetNextRefreshTimeUtc()
        {
            return DateTime.MaxValue;
        }

        /// <summary>
        /// Creates the universes for this algorithm. Called once after <see cref="IAlgorithm.Initialize"/>
        /// </summary>
        /// <param name="algorithm">The algorithm instance to create universes for</param>
        /// <returns>The universes to be used by the algorithm</returns>
        public virtual IEnumerable<Universe> CreateUniverses(QCAlgorithm algorithm)
        {
            throw new NotImplementedException("Types deriving from 'UniverseSelectionModel' must implement the 'IEnumerable<Universe> CreateUniverses(QCAlgorithm) method.");
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
