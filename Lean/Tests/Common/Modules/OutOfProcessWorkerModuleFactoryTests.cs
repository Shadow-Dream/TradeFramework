using System;
using System.Collections.Generic;
using System.IO;
using System.Threading.Tasks;
using QuantConnect.Algorithm.Modules;
using QuantConnect.Modules;
using NUnit.Framework;

namespace QuantConnect.Tests.Common.Modules
{
    [TestFixture]
    public sealed class OutOfProcessWorkerModuleFactoryTests
    {
        [Test]
        public async Task CreatesModuleBackedByJsonLineWorkerProcess()
        {
            var tempDirectory = Path.Combine(Path.GetTempPath(), "lean-worker-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(tempDirectory);
            var workerPath = Path.Combine(tempDirectory, "worker.py");
            File.WriteAllText(workerPath, """
import json
import sys

for line in sys.stdin:
    if not line.strip():
        continue

    request = json.loads(line)
    command = request.get("command") or request.get("Command")
    response = {
        "protocolVersion": request.get("protocolVersion") or request.get("ProtocolVersion"),
        "requestId": request.get("requestId") or request.get("RequestId"),
        "success": True,
        "payload": {},
    }

    if command == "initialize":
        response["payload"] = {"status": "initialized"}
    elif command == "health":
        response["payload"] = {"status": "Healthy"}
    elif command == "snapshot":
        response["payload"] = {"contentType": "application/x.test-worker-snapshot"}
    elif command in {"pause", "resume", "restore"}:
        response["payload"] = {"status": command}
    else:
        response["success"] = False
        response["error"] = "unsupported " + str(command)

    sys.stdout.write(json.dumps(response) + "\n")
    sys.stdout.flush()
""");

            var configuration = new ModuleConfiguration(
                "worker.signal",
                ModuleKind.Signal,
                ModuleActivationMode.OutOfProcessWorker,
                workerPath,
                hotSwapMode: ModuleHotSwapMode.Live,
                parameters: new Dictionary<string, string>
                {
                    [OutOfProcessWorkerModuleFactory.CommandParameter] = "python3",
                    [OutOfProcessWorkerModuleFactory.ArgumentsParameter] = workerPath,
                    [OutOfProcessWorkerModuleFactory.WorkingDirectoryParameter] = tempDirectory
                });

            var factory = new OutOfProcessWorkerModuleFactory();
            IModule module = null;

            try
            {
                Assert.That(factory.CanCreate(configuration), Is.True);

                module = await factory.CreateAsync(configuration).ConfigureAwait(false);
                var health = await module.CheckHealth().ConfigureAwait(false);
                var snapshot = await module.CreateSnapshot().ConfigureAwait(false);

                Assert.That(module.Key, Is.EqualTo("worker.signal"));
                Assert.That(module.ActivationMode, Is.EqualTo(ModuleActivationMode.OutOfProcessWorker));
                Assert.That(health.Status, Is.EqualTo(ModuleHealthStatus.Healthy));
                Assert.That(snapshot.ContentType, Is.EqualTo("application/x.test-worker-snapshot"));
            }
            finally
            {
                (module as IDisposable)?.Dispose();
                Directory.Delete(tempDirectory, true);
            }
        }
    }
}
