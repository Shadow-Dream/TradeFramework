using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using QuantConnect.Modules;
using NUnit.Framework;

namespace QuantConnect.Tests.Common.Modules
{
    [TestFixture]
    public sealed class ExternalAssemblyModuleFactoryTests
    {
        [Test]
        public async Task LoadsModuleFromExternalAssemblyPath()
        {
            var configuration = new ModuleConfiguration(
                "plugin.signal",
                ModuleKind.Signal,
                ModuleActivationMode.InProcessPlugin,
                typeof(PluginSignalModule).FullName,
                parameters: new Dictionary<string, string>
                {
                    [ExternalAssemblyModuleFactory.AssemblyPathParameter] = typeof(ExternalAssemblyModuleFactoryTests).Assembly.Location
                });

            using var handle = (PluginModuleHandle)await new ExternalAssemblyModuleFactory()
                .CreateAsync(configuration)
                .ConfigureAwait(false);

            Assert.That(handle.Key, Is.EqualTo("plugin.signal"));
            Assert.That(handle.Kind, Is.EqualTo(ModuleKind.Signal));
            Assert.That(handle.ActivationMode, Is.EqualTo(ModuleActivationMode.InProcessPlugin));
        }

        private sealed class PluginSignalModule : IModule
        {
            private readonly ModuleState _state = new(typeof(PluginSignalModule), ModuleKind.Signal, ModuleHotSwapMode.Live);

            public string Key => _state.Key;
            public ModuleKind Kind => _state.Kind;
            public ModuleActivationMode ActivationMode => _state.ActivationMode;
            public string Version => _state.Version;
            public ModuleHotSwapMode HotSwapMode => _state.HotSwapMode;

            public ValueTask Initialize(ModuleConfiguration configuration, CancellationToken cancellationToken = default)
            {
                return _state.Initialize(configuration, cancellationToken);
            }

            public ValueTask Pause(CancellationToken cancellationToken = default) => _state.Pause(cancellationToken);
            public ValueTask Resume(CancellationToken cancellationToken = default) => _state.Resume(cancellationToken);
            public ValueTask<ModuleSnapshot> CreateSnapshot(CancellationToken cancellationToken = default) => _state.CreateSnapshot(cancellationToken);
            public ValueTask RestoreSnapshot(ModuleSnapshot snapshot, CancellationToken cancellationToken = default) => _state.RestoreSnapshot(snapshot, cancellationToken);
            public ValueTask<ModuleHealthCheckResult> CheckHealth(CancellationToken cancellationToken = default) => _state.CheckHealth(cancellationToken);
        }
    }
}
