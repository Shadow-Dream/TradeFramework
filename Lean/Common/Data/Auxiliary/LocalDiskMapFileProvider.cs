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
 *
*/

using System.IO;
using System.Threading;
using QuantConnect.Logging;
using QuantConnect.Interfaces;
using System.Collections.Concurrent;
using QuantConnect.Modules;
using System.Threading.Tasks;

namespace QuantConnect.Data.Auxiliary
{
    /// <summary>
    /// Provides a default implementation of <see cref="IMapFileProvider"/> that reads from
    /// the local disk
    /// </summary>
    public class LocalDiskMapFileProvider : IMapFileProvider, IModule
    {
        private static int _wroteTraceStatement;
        private readonly ConcurrentDictionary<AuxiliaryDataKey, MapFileResolver> _cache;
        private IDataProvider _dataProvider;
        private readonly ModuleState _moduleState;

        /// <summary>
        /// Creates a new instance of the <see cref="LocalDiskFactorFileProvider"/>
        /// </summary>
        public LocalDiskMapFileProvider()
        {
            _moduleState = new ModuleState(GetType(), ModuleKind.DataSource, ModuleHotSwapMode.Live);
            _cache = new ConcurrentDictionary<AuxiliaryDataKey, MapFileResolver>();
        }

        public string Key => _moduleState.Key;
        public ModuleKind Kind => _moduleState.Kind;
        public ModuleActivationMode ActivationMode => _moduleState.ActivationMode;
        public string Version => _moduleState.Version;
        public ModuleHotSwapMode HotSwapMode => _moduleState.HotSwapMode;

        /// <summary>
        /// Initializes our MapFileProvider by supplying our dataProvider
        /// </summary>
        /// <param name="dataProvider">DataProvider to use</param>
        public void Initialize(IDataProvider dataProvider)
        {
            _dataProvider = dataProvider;
        }

        /// <summary>
        /// Gets a <see cref="MapFileResolver"/> representing all the map
        /// files for the specified market
        /// </summary>
        /// <param name="auxiliaryDataKey">Key used to fetch a map file resolver. Specifying market and security type</param>
        /// <returns>A <see cref="MapFileRow"/> containing all map files for the specified market</returns>
        public MapFileResolver Get(AuxiliaryDataKey auxiliaryDataKey)
        {
            return _cache.GetOrAdd(auxiliaryDataKey, GetMapFileResolver);
        }

        private MapFileResolver GetMapFileResolver(AuxiliaryDataKey key)
        {
            var securityType = key.SecurityType;
            var market = key.Market;

            var mapFileDirectory = Globals.GetDataFolderPath(MapFile.GetRelativeMapFilePath(market, securityType));
            if (!Directory.Exists(mapFileDirectory))
            {
                // only write this message once per application instance
                if (Interlocked.CompareExchange(ref _wroteTraceStatement, 1, 0) == 0)
                {
                    Log.Error($"LocalDiskMapFileProvider.GetMapFileResolver({market}): " +
                        $"The specified directory does not exist: {mapFileDirectory}"
                    );
                }
                return MapFileResolver.Empty;
            }
            return new MapFileResolver(MapFile.GetMapFiles(mapFileDirectory, market, securityType, _dataProvider));
        }

        public ValueTask Initialize(ModuleConfiguration configuration, CancellationToken cancellationToken = default)
        {
            return _moduleState.Initialize(configuration, cancellationToken);
        }

        public ValueTask Pause(CancellationToken cancellationToken = default)
        {
            return _moduleState.Pause(cancellationToken);
        }

        public ValueTask Resume(CancellationToken cancellationToken = default)
        {
            return _moduleState.Resume(cancellationToken);
        }

        public ValueTask<ModuleSnapshot> CreateSnapshot(CancellationToken cancellationToken = default)
        {
            return _moduleState.CreateSnapshot(cancellationToken);
        }

        public ValueTask RestoreSnapshot(ModuleSnapshot snapshot, CancellationToken cancellationToken = default)
        {
            return _moduleState.RestoreSnapshot(snapshot, cancellationToken);
        }

        public ValueTask<ModuleHealthCheckResult> CheckHealth(CancellationToken cancellationToken = default)
        {
            return _moduleState.CheckHealth(cancellationToken);
        }
    }
}
