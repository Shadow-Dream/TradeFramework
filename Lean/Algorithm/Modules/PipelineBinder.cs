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
using System.Runtime.CompilerServices;
using System.Linq;
using QuantConnect.Algorithm.Framework.Alphas;
using QuantConnect.Algorithm.Framework.Execution;
using QuantConnect.Algorithm.Framework.Portfolio;
using QuantConnect.Algorithm.Framework.Risk;
using QuantConnect.Algorithm.Framework.Selection;
using QuantConnect.Brokerages;
using QuantConnect.Data;
using QuantConnect.Modules;

namespace QuantConnect.Algorithm.Modules
{
    /// <summary>
    /// Binds a loaded pipeline into a <see cref="QCAlgorithm"/> using existing Lean extension points.
    /// </summary>
    public static class PipelineBinder
    {
        private static readonly ConditionalWeakTable<QCAlgorithm, PipelineBindingState> State = new();

        public static void Bind(QCAlgorithm algorithm, LoadedPipeline pipeline)
        {
            ArgumentNullException.ThrowIfNull(algorithm);
            ArgumentNullException.ThrowIfNull(pipeline);

            BindMarketRule(algorithm, pipeline);
            BindInputs(algorithm, pipeline);
            BindUniverse(algorithm, pipeline);
            BindSignals(algorithm, pipeline);
            BindTargets(algorithm, pipeline);
            BindConstraints(algorithm, pipeline);
            BindExecution(algorithm, pipeline);
        }

        private static void BindInputs(QCAlgorithm algorithm, LoadedPipeline pipeline)
        {
            var modules = ResolveMany<IInputModule>(pipeline, pipeline.Manifest.InputModules);
            var nextState = State.GetValue(algorithm, _ => new PipelineBindingState());

            var nextSymbolsByModule = new Dictionary<string, HashSet<Symbol>>(StringComparer.Ordinal);
            foreach (var module in modules)
            {
                var inputs = module.CreateInputs(algorithm) ?? Array.Empty<InputRegistration>();
                var symbols = new HashSet<Symbol>();
                foreach (var input in inputs)
                {
                    if (input.Symbol == null)
                    {
                        throw new InvalidOperationException($"Input module '{module.Key}' returned a null symbol.");
                    }

                    symbols.Add(input.Symbol);
                    if (!algorithm.Securities.ContainsKey(input.Symbol))
                    {
                        algorithm.AddSecurity(input.Symbol, input.Resolution, input.FillForward, input.Leverage, input.ExtendedMarketHours);
                    }
                }

                nextSymbolsByModule[module.Key] = symbols;
            }

            foreach (var (moduleKey, previousSymbols) in nextState.InputSymbolsByModule.ToArray())
            {
                if (nextSymbolsByModule.TryGetValue(moduleKey, out var nextSymbols))
                {
                    previousSymbols.ExceptWith(nextSymbols);
                    foreach (var symbol in previousSymbols)
                    {
                        if (algorithm.Portfolio[symbol].Invested || algorithm.Transactions.GetOpenOrders(x => x.Symbol == symbol).Any())
                        {
                            throw new InvalidOperationException($"Cannot remove input symbol '{symbol}' while the portfolio is invested or there are open orders.");
                        }

                        if (algorithm.Securities.ContainsKey(symbol))
                        {
                            algorithm.RemoveSecurity(symbol);
                        }
                    }

                    nextState.InputSymbolsByModule[moduleKey] = new HashSet<Symbol>(nextSymbols);
                }
                else
                {
                    foreach (var symbol in previousSymbols)
                    {
                        if (algorithm.Portfolio[symbol].Invested || algorithm.Transactions.GetOpenOrders(x => x.Symbol == symbol).Any())
                        {
                            throw new InvalidOperationException($"Cannot remove input symbol '{symbol}' while the portfolio is invested or there are open orders.");
                        }

                        if (algorithm.Securities.ContainsKey(symbol))
                        {
                            algorithm.RemoveSecurity(symbol);
                        }
                    }

                    nextState.InputSymbolsByModule.Remove(moduleKey);
                }
            }

            foreach (var (moduleKey, nextSymbols) in nextSymbolsByModule)
            {
                if (!nextState.InputSymbolsByModule.ContainsKey(moduleKey))
                {
                    nextState.InputSymbolsByModule[moduleKey] = new HashSet<Symbol>(nextSymbols);
                }
            }
        }

        private static void BindUniverse(QCAlgorithm algorithm, LoadedPipeline pipeline)
        {
            var models = ResolveMany<IUniverseSelectionModel>(pipeline, pipeline.Manifest.UniverseModules);
            if (models.Count == 0) return;
            algorithm.SetUniverseSelection(models[0]);
            for (var i = 1; i < models.Count; i++) algorithm.AddUniverseSelection(models[i]);
        }

        private static void BindSignals(QCAlgorithm algorithm, LoadedPipeline pipeline)
        {
            var models = ResolveMany<IAlphaModel>(pipeline, pipeline.Manifest.SignalModules);
            if (models.Count == 0) return;
            algorithm.SetAlpha(models[0]);
            for (var i = 1; i < models.Count; i++) algorithm.AddAlpha(models[i]);
        }

        private static void BindTargets(QCAlgorithm algorithm, LoadedPipeline pipeline)
        {
            var keys = pipeline.Manifest.TargetModules;
            if (keys.Count == 0) return;
            if (keys.Count == 1)
            {
                algorithm.SetPortfolioConstruction(ResolveSingle<IPortfolioConstructionModel>(pipeline, keys[0]));
                return;
            }

            var intentModels = ResolveMany<IPortfolioIntentModel>(pipeline, keys);
            algorithm.SetPortfolioConstruction(new CompositePortfolioConstructionModule(intentModels));
        }

        private static void BindConstraints(QCAlgorithm algorithm, LoadedPipeline pipeline)
        {
            var models = ResolveMany<IRiskManagementModel>(pipeline, pipeline.Manifest.ConstraintModules);
            if (models.Count == 0) return;
            algorithm.SetRiskManagement(models[0]);
            for (var i = 1; i < models.Count; i++) algorithm.AddRiskManagement(models[i]);
        }

        private static void BindExecution(QCAlgorithm algorithm, LoadedPipeline pipeline)
        {
            var keys = pipeline.Manifest.ExecutionModules;
            if (keys.Count == 0) return;
            if (keys.Count > 1)
            {
                throw new NotSupportedException("Multiple execution modules are not yet supported without an execution coordinator.");
            }

            algorithm.SetExecution(ResolveSingle<IExecutionModel>(pipeline, keys[0]));
        }

        private static void BindMarketRule(QCAlgorithm algorithm, LoadedPipeline pipeline)
        {
            if (string.IsNullOrWhiteSpace(pipeline.Manifest.MarketRuleModule)) return;
            algorithm.SetBrokerageModel(ResolveSingle<IBrokerageModel>(pipeline, pipeline.Manifest.MarketRuleModule));
        }

        private static T ResolveSingle<T>(LoadedPipeline pipeline, string key)
        {
            if (!pipeline.Modules.TryGetValue(key, out var module))
            {
                throw new KeyNotFoundException($"Pipeline module '{key}' is not loaded.");
            }

            return module is T typed
                ? typed
                : UnwrapModule<T>(module, key);
        }

        private static List<T> ResolveMany<T>(LoadedPipeline pipeline, IReadOnlyList<string> keys)
        {
            var result = new List<T>(keys.Count);
            foreach (var key in keys)
            {
                result.Add(ResolveSingle<T>(pipeline, key));
            }
            return result;
        }

        private sealed class PipelineBindingState
        {
            public Dictionary<string, HashSet<Symbol>> InputSymbolsByModule { get; } = new(StringComparer.Ordinal);
        }

        private static T UnwrapModule<T>(IModule module, string key)
        {
            if (module is QuantConnect.Modules.PluginModuleHandle pluginHandle && pluginHandle.Inner is T typed)
            {
                return typed;
            }

            throw new InvalidOperationException($"Pipeline module '{key}' does not implement {typeof(T).Name}.");
        }
    }
}
