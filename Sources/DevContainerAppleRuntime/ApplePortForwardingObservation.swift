// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import DevContainerModel
import DevContainerRuntimeSPI
import Foundation

struct ApplePortForwardingObservation {
    let generation: UUID
    let task: Task<Void, Never>
}

extension AppleContainerRuntime {
    /// Restored and CLI-started containers lack an attached process waiter.
    /// Observe their exact incarnation without manufacturing an exit status.
    func observePortForwardingExit(snapshot: ContainerSnapshot, generation: UUID) {
        let id = snapshot.runtimeID.rawValue
        let task = Task { [weak self] in
            while !Task.isCancelled {
                guard let shouldContinue = await self?.reconcilePortForwarding(
                    snapshot: snapshot, generation: generation
                ), shouldContinue else { break }
                do { try await Task.sleep(for: .milliseconds(200)) } catch { break }
            }
            await self?.finishPortForwardingObservation(id: id, generation: generation)
        }
        portForwardingObservers[id] = .init(generation: generation, task: task)
        // Retired observers remain joinable until their last inspection returns.
        portForwardingObservationTasks[generation] = task
    }

    private func reconcilePortForwarding(snapshot: ContainerSnapshot, generation: UUID) async -> Bool {
        let id = snapshot.runtimeID.rawValue
        guard portForwardingObservers[id]?.generation == generation, !Task.isCancelled,
              await portForwarding.hasListeners(containerID: id)
        else { return false }
        do {
            let current = try await inspectContainer(id: id, context: RuntimeRequestContext())
            if current.state == .running, current.createdAt == snapshot.createdAt,
               current.startedAt == snapshot.startedAt
            {
                return true
            }
        } catch let error as DevContainerError where error.code == .notFound {
            // Confirmed absence invalidates this listener incarnation only.
        } catch {
            // A transport failure is not exit. Keep ownership until a later
            // authoritative observation or explicit adapter shutdown.
            return !Task.isCancelled
        }
        await portForwarding.stop(containerID: id, generation: generation)
        return false
    }

    private func finishPortForwardingObservation(id: String, generation: UUID) {
        portForwardingObservationTasks.removeValue(forKey: generation)
        if portForwardingObservers[id]?.generation == generation {
            portForwardingObservers.removeValue(forKey: id)
        }
    }
}
