using System;
using System.Collections.Generic;
using QuantConnect.Modules;
using NUnit.Framework;

namespace QuantConnect.Tests.Common.Modules
{
    [TestFixture]
    public sealed class ModuleConfigurationTests
    {
        [Test]
        public void CreatesImmutableCopiesOfParametersAndDependencies()
        {
            var parameters = new Dictionary<string, string> { ["mode"] = "demo" };
            var dependencies = new List<string> { "data", "alpha", "alpha" };

            var configuration = new ModuleConfiguration(
                "bitget",
                ModuleKind.MarketRule,
                ModuleActivationMode.RemoteService,
                "grpc://bitget",
                parameters: parameters,
                dependencies: dependencies);

            parameters["mode"] = "mutated";
            dependencies.Add("extra");

            Assert.That(configuration.Parameters["mode"], Is.EqualTo("demo"));
            Assert.That(configuration.Dependencies.Count, Is.EqualTo(2));
        }

        [Test]
        public void RejectsMissingKey()
        {
            Assert.Throws<ArgumentException>(() =>
                new ModuleConfiguration("", ModuleKind.Signal, ModuleActivationMode.RemoteService, "svc"));
        }

        [Test]
        public void RejectsMissingEntryPoint()
        {
            Assert.Throws<ArgumentException>(() =>
                new ModuleConfiguration("alpha", ModuleKind.Signal, ModuleActivationMode.RemoteService, ""));
        }
    }
}
