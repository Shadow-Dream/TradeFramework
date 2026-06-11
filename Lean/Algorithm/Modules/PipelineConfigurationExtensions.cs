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

using QuantConnect.Configuration;
using QuantConnect.Modules;

namespace QuantConnect.Algorithm.Modules
{
    /// <summary>
    /// Loads and binds a pipeline manifest when configured for the current process.
    /// </summary>
    public static class PipelineConfigurationExtensions
    {
        public static LoadedPipeline TryLoadConfiguredPipeline()
        {
            var lanesManifestPath = Config.Get("pipeline-lanes-manifest");
            var manifestPath = Config.Get("pipeline-manifest");
            if (string.IsNullOrWhiteSpace(lanesManifestPath) && string.IsNullOrWhiteSpace(manifestPath))
            {
                return null;
            }

            var manifest = string.IsNullOrWhiteSpace(lanesManifestPath)
                ? PipelineManifestJsonLoader.Load(manifestPath)
                : PipelineLanesManifestJsonLoader.Load(lanesManifestPath);
            var runtime = new PipelineRuntime(new ModuleControlPlane(
                new RemoteServiceModuleFactory(),
                new ScriptRunnerModuleFactory(),
                new OutOfProcessWorkerModuleFactory(),
                new ReflectionModuleFactory(),
                new ExternalAssemblyModuleFactory()));
            return runtime.LoadAsync(manifest).GetAwaiter().GetResult();
        }
    }
}
