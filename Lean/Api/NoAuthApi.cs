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
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.ComponentModel.Composition;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using QuantConnect.Interfaces;
using QuantConnect.Logging;
using QuantConnect.Notifications;
using QuantConnect.Optimizer.Objectives;
using QuantConnect.Optimizer.Parameters;
using QuantConnect.Statistics;

namespace QuantConnect.Api
{
    /// <summary>
    /// Local-only API handler that disables all authenticated QuantConnect services.
    /// It keeps the engine runnable while making every cloud/API-dependent feature
    /// fail explicitly with a warning.
    /// </summary>
    [Export(typeof(IApi))]
    public class NoAuthApi : IApi, IDownloadProvider
    {
        private const string DisabledMessage =
            "QuantConnect authenticated services are disabled in this local build.";

        private static readonly ConcurrentDictionary<string, byte> WarnedFeatures = new();

        private readonly HttpClient _httpClient = new();

        public void Initialize(int userId, string token, string dataFolder)
        {
            if (userId != 0 || !string.IsNullOrWhiteSpace(token))
            {
                WarnOnce("InitializeCredentialsIgnored",
                    "Ignoring job-user-id/api-access-token because QuantConnect authenticated services are disabled.");
            }
        }

        public ProjectResponse CreateProject(string name, Language language, string organizationId = null)
            => DisabledResult<ProjectResponse>(nameof(CreateProject));

        public ProjectResponse ReadProject(int projectId)
            => DisabledResult<ProjectResponse>(nameof(ReadProject));

        public RestResponse AddProjectFile(int projectId, string name, string content)
            => DisabledResult<RestResponse>(nameof(AddProjectFile));

        public RestResponse UpdateProjectFileName(int projectId, string oldFileName, string newFileName)
            => DisabledResult<RestResponse>(nameof(UpdateProjectFileName));

        public RestResponse UpdateProjectFileContent(int projectId, string fileName, string newFileContents)
            => DisabledResult<RestResponse>(nameof(UpdateProjectFileContent));

        public ProjectFilesResponse ReadProjectFile(int projectId, string fileName)
            => DisabledResult<ProjectFilesResponse>(nameof(ReadProjectFile));

        public ProjectFilesResponse ReadProjectFiles(int projectId)
            => DisabledResult<ProjectFilesResponse>(nameof(ReadProjectFiles));

        public ProjectNodesResponse ReadProjectNodes(int projectId)
            => DisabledResult<ProjectNodesResponse>(nameof(ReadProjectNodes));

        public ProjectNodesResponse UpdateProjectNodes(int projectId, string[] nodes)
            => DisabledResult<ProjectNodesResponse>(nameof(UpdateProjectNodes));

        public RestResponse DeleteProjectFile(int projectId, string name)
            => DisabledResult<RestResponse>(nameof(DeleteProjectFile));

        public RestResponse DeleteProject(int projectId)
            => DisabledResult<RestResponse>(nameof(DeleteProject));

        public ProjectResponse ListProjects()
            => DisabledResult<ProjectResponse>(nameof(ListProjects));

        public Compile CreateCompile(int projectId)
            => DisabledResult<Compile>(nameof(CreateCompile));

        public Compile ReadCompile(int projectId, string compileId)
            => DisabledResult<Compile>(nameof(ReadCompile));

        public Backtest CreateBacktest(int projectId, string compileId, string backtestName)
            => DisabledResult<Backtest>(nameof(CreateBacktest));

        public Backtest ReadBacktest(int projectId, string backtestId, bool getCharts = true)
            => DisabledResult<Backtest>(nameof(ReadBacktest));

        public RestResponse UpdateBacktest(int projectId, string backtestId, string name = "", string note = "")
            => DisabledResult<RestResponse>(nameof(UpdateBacktest));

        public RestResponse DeleteBacktest(int projectId, string backtestId)
            => DisabledResult<RestResponse>(nameof(DeleteBacktest));

        public BacktestSummaryList ListBacktests(int projectId, bool includeStatistics = false)
            => DisabledResult<BacktestSummaryList>(nameof(ListBacktests));

        public InsightResponse ReadBacktestInsights(int projectId, string backtestId, int start = 0, int end = 0)
            => DisabledResult<InsightResponse>(nameof(ReadBacktestInsights));

        public Estimate EstimateOptimization(int projectId, string name, string target, string targetTo,
            decimal? targetValue, string strategy, string compileId,
            HashSet<OptimizationParameter> parameters, IReadOnlyList<Constraint> constraints)
        {
            WarnOnce(nameof(EstimateOptimization), FeatureMessage(nameof(EstimateOptimization)));
            return new Estimate();
        }

        public OptimizationSummary CreateOptimization(int projectId, string name, string target, string targetTo,
            decimal? targetValue, string strategy, string compileId,
            HashSet<OptimizationParameter> parameters, IReadOnlyList<Constraint> constraints,
            decimal estimatedCost, string nodeType, int parallelNodes)
            => DisabledResult<OptimizationSummary>(nameof(CreateOptimization));

        public List<OptimizationSummary> ListOptimizations(int projectId)
        {
            WarnOnce(nameof(ListOptimizations), FeatureMessage(nameof(ListOptimizations)));
            return new List<OptimizationSummary>();
        }

        public Optimization ReadOptimization(string optimizationId)
            => DisabledResult<Optimization>(nameof(ReadOptimization));

        public RestResponse AbortOptimization(string optimizationId)
            => DisabledResult<RestResponse>(nameof(AbortOptimization));

        public RestResponse UpdateOptimization(string optimizationId, string name = null)
            => DisabledResult<RestResponse>(nameof(UpdateOptimization));

        public RestResponse DeleteOptimization(string optimizationId)
            => DisabledResult<RestResponse>(nameof(DeleteOptimization));

        public LiveLog ReadLiveLogs(int projectId, string algorithmId, int startLine, int endLine)
            => DisabledResult<LiveLog>(nameof(ReadLiveLogs));

        public ReadChartResponse ReadLiveChart(int projectId, string name, int start, int end, uint count)
            => DisabledResult<ReadChartResponse>(nameof(ReadLiveChart));

        public PortfolioResponse ReadLivePortfolio(int projectId)
            => DisabledResult<PortfolioResponse>(nameof(ReadLivePortfolio));

        public InsightResponse ReadLiveInsights(int projectId, int start = 0, int end = 0)
            => DisabledResult<InsightResponse>(nameof(ReadLiveInsights));

        public DataLink ReadDataLink(string filePath, string organizationId)
            => DisabledResult<DataLink>(nameof(ReadDataLink));

        public DataList ReadDataDirectory(string filePath)
            => DisabledResult<DataList>(nameof(ReadDataDirectory));

        public DataPricesList ReadDataPrices(string organizationId)
            => DisabledResult<DataPricesList>(nameof(ReadDataPrices));

        public BacktestReport ReadBacktestReport(int projectId, string backtestId)
            => DisabledResult<BacktestReport>(nameof(ReadBacktestReport));

        public ReadChartResponse ReadBacktestChart(int projectId, string name, int start, int end, uint count, string backtestId)
            => DisabledResult<ReadChartResponse>(nameof(ReadBacktestChart));

        public bool DownloadData(string filePath, string organizationId)
        {
            WarnOnce(nameof(DownloadData), FeatureMessage(nameof(DownloadData)));
            return false;
        }

        public Account ReadAccount(string organizationId = null)
            => DisabledResult<Account>(nameof(ReadAccount));

        public Organization ReadOrganization(string organizationId = null)
        {
            WarnOnce(nameof(ReadOrganization), FeatureMessage(nameof(ReadOrganization)));
            return new Organization
            {
                Products = new List<Product>()
            };
        }

        public CreateLiveAlgorithmResponse CreateLiveAlgorithm(int projectId, string compileId, string nodeId,
            Dictionary<string, object> brokerageSettings, string versionId = "-1", Dictionary<string, object> dataProviders = null)
            => DisabledResult<CreateLiveAlgorithmResponse>(nameof(CreateLiveAlgorithm));

        public LiveList ListLiveAlgorithms(AlgorithmStatus? status = null)
            => DisabledResult<LiveList>(nameof(ListLiveAlgorithms));

        public LiveAlgorithmResults ReadLiveAlgorithm(int projectId, string deployId)
            => DisabledResult<LiveAlgorithmResults>(nameof(ReadLiveAlgorithm));

        public RestResponse LiquidateLiveAlgorithm(int projectId)
            => DisabledResult<RestResponse>(nameof(LiquidateLiveAlgorithm));

        public RestResponse StopLiveAlgorithm(int projectId)
            => DisabledResult<RestResponse>(nameof(StopLiveAlgorithm));

        public RestResponse SendNotification(Notification notification, int projectId)
            => DisabledResult<RestResponse>(nameof(SendNotification));

        public AlgorithmControl GetAlgorithmStatus(string algorithmId)
        {
            WarnOnce(nameof(GetAlgorithmStatus), FeatureMessage(nameof(GetAlgorithmStatus)));
            return new AlgorithmControl
            {
                Initialized = false,
                HasSubscribers = false
            };
        }

        public void SetAlgorithmStatus(string algorithmId, AlgorithmStatus status, string message = "")
        {
            WarnOnce(nameof(SetAlgorithmStatus), FeatureMessage(nameof(SetAlgorithmStatus)));
        }

        public void SendStatistics(string algorithmId, decimal unrealized, decimal fees, decimal netProfit,
            decimal holdings, decimal equity, decimal netReturn, decimal volume, int trades, double sharpe)
        {
            WarnOnce(nameof(SendStatistics), FeatureMessage(nameof(SendStatistics)));
        }

        public string Download(string address, IEnumerable<KeyValuePair<string, string>> headers, string userName, string password)
        {
            return Encoding.UTF8.GetString(DownloadBytes(address, headers, userName, password));
        }

        public byte[] DownloadBytes(string address, IEnumerable<KeyValuePair<string, string>> headers, string userName, string password)
        {
            if (IsQuantConnectAddress(address))
            {
                WarnOnce(nameof(DownloadBytes) + ".QuantConnect",
                    $"Blocked attempt to download from QuantConnect endpoint '{address}' because {DisabledMessage}");
                throw new InvalidOperationException(
                    $"Blocked QuantConnect download: '{address}'. {DisabledMessage}");
            }

            try
            {
                using var request = new HttpRequestMessage(HttpMethod.Get, address);

                if (headers != null)
                {
                    foreach (var header in headers)
                    {
                        request.Headers.TryAddWithoutValidation(header.Key, header.Value);
                    }
                }

                if (!request.Headers.Contains("User-Agent"))
                {
                    request.Headers.TryAddWithoutValidation("User-Agent", "LEAN Local NoAuthApi");
                }

                if (!string.IsNullOrEmpty(userName) || !string.IsNullOrEmpty(password))
                {
                    var credentials = Convert.ToBase64String(Encoding.ASCII.GetBytes($"{userName}:{password}"));
                    request.Headers.Authorization = new AuthenticationHeaderValue("Basic", credentials);
                }

                using var response = _httpClient.SendAsync(request).GetAwaiter().GetResult();
                response.EnsureSuccessStatusCode();
                return response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult();
            }
            catch (Exception exception)
            {
                var message = $"NoAuthApi.DownloadBytes(): Failed to download data from {address}";
                if (!string.IsNullOrEmpty(userName) || !string.IsNullOrEmpty(password))
                {
                    message += $" with username: {userName} and password: {(string.IsNullOrEmpty(password) ? "" : new string('*', password.Length))}";
                }

                throw new WebException($"{message}. Please verify the source URL.", exception);
            }
        }

        public bool GetObjectStore(string organizationId, List<string> keys, string destinationFolder = null)
        {
            WarnOnce(nameof(GetObjectStore), FeatureMessage(nameof(GetObjectStore)));
            return false;
        }

        public PropertiesObjectStoreResponse GetObjectStoreProperties(string organizationId, string key)
            => DisabledResult<PropertiesObjectStoreResponse>(nameof(GetObjectStoreProperties));

        public RestResponse SetObjectStore(string organizationId, string key, byte[] objectData)
            => DisabledResult<RestResponse>(nameof(SetObjectStore));

        public RestResponse DeleteObjectStore(string organizationId, string key)
            => DisabledResult<RestResponse>(nameof(DeleteObjectStore));

        public VersionsResponse ReadLeanVersions()
            => DisabledResult<VersionsResponse>(nameof(ReadLeanVersions));

        public RestResponse BroadcastLiveCommand(string organizationId, int? excludeProjectId, object command)
            => DisabledResult<RestResponse>(nameof(BroadcastLiveCommand));

        public void Dispose()
        {
            _httpClient.Dispose();
        }

        private static T DisabledResult<T>(string feature) where T : RestResponse, new()
        {
            var response = new T { Success = false };
            response.Errors.Add(FeatureMessage(feature));
            WarnOnce(feature, response.Errors[0]);
            return response;
        }

        private static string FeatureMessage(string feature)
        {
            return $"{DisabledMessage} The feature '{feature}' is marked local-only and currently skipped.";
        }

        private static void WarnOnce(string key, string message)
        {
            if (WarnedFeatures.TryAdd(key, 0))
            {
                Log.Error(message);
            }
        }

        private static bool IsQuantConnectAddress(string address)
        {
            if (string.IsNullOrWhiteSpace(address))
            {
                return false;
            }

            if (!Uri.TryCreate(address, UriKind.Absolute, out var uri))
            {
                return address.Contains("quantconnect.com", StringComparison.InvariantCultureIgnoreCase);
            }

            return uri.Host.Contains("quantconnect.com", StringComparison.InvariantCultureIgnoreCase);
        }
    }
}
