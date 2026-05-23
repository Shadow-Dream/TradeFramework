using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using QuantConnect.Modules;
using NUnit.Framework;

namespace QuantConnect.Tests.Common.Modules
{
    [TestFixture]
    public sealed class ModuleControlPlaneTests
    {
        [Test]
        public async Task ReflectionFactoryCreatesBuiltInModule()
        {
            var configuration = new ModuleConfiguration(
                "test.alpha",
                ModuleKind.Signal,
                ModuleActivationMode.BuiltIn,
                typeof(TestModule).FullName);

            var module = await new ReflectionModuleFactory().CreateAsync(configuration).ConfigureAwait(false);

            Assert.That(module.Key, Is.EqualTo("test.alpha"));
            Assert.That(module.Kind, Is.EqualTo(ModuleKind.Signal));
        }

        [Test]
        public async Task ControlPlaneLoadsAndControlsModuleLifecycle()
        {
            var controlPlane = new ModuleControlPlane(new ReflectionModuleFactory());
            var configuration = new ModuleConfiguration(
                "test.signal",
                ModuleKind.Signal,
                ModuleActivationMode.BuiltIn,
                typeof(TestModule).FullName);

            await controlPlane.LoadAsync(configuration).ConfigureAwait(false);
            await controlPlane.Pause("test.signal").ConfigureAwait(false);
            var snapshot = await controlPlane.SnapshotAsync("test.signal").ConfigureAwait(false);
            await controlPlane.RestoreAsync("test.signal", snapshot).ConfigureAwait(false);
            await controlPlane.Resume("test.signal").ConfigureAwait(false);
            var health = await controlPlane.CheckHealth("test.signal").ConfigureAwait(false);
            await controlPlane.UnloadAsync("test.signal").ConfigureAwait(false);

            Assert.That(snapshot.ModuleKey, Is.EqualTo("test.signal"));
            Assert.That(health.Status, Is.EqualTo(ModuleHealthStatus.Healthy));
        }

        [Test]
        public void ReflectionFactoryRejectsTypesWithoutModuleContract()
        {
            var configuration = new ModuleConfiguration(
                "bad",
                ModuleKind.Signal,
                ModuleActivationMode.BuiltIn,
                typeof(object).FullName);

            Assert.ThrowsAsync<System.InvalidOperationException>(async () => await new ReflectionModuleFactory().CreateAsync(configuration).ConfigureAwait(false));
        }

        private sealed class TestModule : IModule
        {
            private readonly ModuleState _state = new(typeof(TestModule), ModuleKind.Signal, ModuleHotSwapMode.Live);

            public string Key => _state.Key;
            public ModuleKind Kind => _state.Kind;
            public ModuleActivationMode ActivationMode => _state.ActivationMode;
            public string Version => _state.Version;
            public ModuleHotSwapMode HotSwapMode => _state.HotSwapMode;

            public ValueTask Initialize(ModuleConfiguration configuration, CancellationToken cancellationToken = default)
            {
                return _state.Initialize(configuration, cancellationToken);
            }

            public ValueTask Pause(CancellationToken cancellationToken = default)
            {
                return _state.Pause(cancellationToken);
            }

            public ValueTask Resume(CancellationToken cancellationToken = default)
            {
                return _state.Resume(cancellationToken);
            }

            public ValueTask<ModuleSnapshot> CreateSnapshot(CancellationToken cancellationToken = default)
            {
                return _state.CreateSnapshot(cancellationToken);
            }

            public ValueTask RestoreSnapshot(ModuleSnapshot snapshot, CancellationToken cancellationToken = default)
            {
                return _state.RestoreSnapshot(snapshot, cancellationToken);
            }

            public ValueTask<ModuleHealthCheckResult> CheckHealth(CancellationToken cancellationToken = default)
            {
                return _state.CheckHealth(cancellationToken);
            }
        }
    }
}
