// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import CSQLite
import DevContainerModel
import Foundation

extension SQLiteContainerOutputJournal {
    func beginLogGeneration() throws {
        try connection.statement("""
        INSERT INTO runtime_output_generations(docker_id, generation, sources) VALUES (?, ?, ?)
        """) { statement in
            try connection.bind(snapshot.dockerID.rawValue, to: statement, at: 1)
            try connection.bind(generation, to: statement, at: 2)
            try connection.bind(snapshot.spec.terminal ? 1 : 3, to: statement, at: 3)
            try connection.done(statement)
        }
    }

    func appendLogs(_ frames: [RuntimeIOFrame]) throws {
        for frame in frames {
            try connection.statement("""
            UPDATE runtime_output_journals SET log_sequence = log_sequence + 1, log_bytes = log_bytes + ?
            WHERE docker_id = ? AND generation = ? AND complete = 0 AND log_version = 1
            AND log_bytes <= ? AND log_sequence < ?
            """) { statement in
                try connection.bind(Int64(frame.data.count), to: statement, at: 1)
                try connection.bind(snapshot.dockerID.rawValue, to: statement, at: 2)
                try connection.bind(generation, to: statement, at: 3)
                try connection.bind(Self.maximumStoredBytes - Int64(frame.data.count), to: statement, at: 4)
                try connection.bind(Self.maximumStoredFrames, to: statement, at: 5)
                try connection.done(statement)
                guard sqlite3_changes(connection.database) == 1 else {
                    throw SQLiteOutputConnection.failure("Log capture changed or its storage bound was exceeded")
                }
            }
            try connection.statement("""
            INSERT INTO runtime_output_logs(docker_id, generation, sequence, channel, payload)
            SELECT docker_id, generation, log_sequence, ?, ? FROM runtime_output_journals
            WHERE docker_id = ? AND generation = ?
            """) { statement in
                try connection.bind(Int64(frame.channel.rawValue), to: statement, at: 1)
                try connection.bind(frame.data, to: statement, at: 2)
                try connection.bind(snapshot.dockerID.rawValue, to: statement, at: 3)
                try connection.bind(generation, to: statement, at: 4)
                try connection.done(statement)
            }
        }
    }

    func persistSourceEOF(_ channel: RuntimeIOChannel) throws {
        try connection.statement("""
        UPDATE runtime_output_generations SET ended = ended | ?
        WHERE docker_id = ? AND generation = ? AND sources & ? != 0 AND ended & ? = 0
        AND EXISTS (SELECT 1 FROM runtime_output_journals j
                    WHERE j.docker_id = runtime_output_generations.docker_id
                    AND j.generation = runtime_output_generations.generation AND j.complete = 0)
        """) { statement in
            let bit = Int64(channel.rawValue)
            try connection.bind(bit, to: statement, at: 1)
            try connection.bind(snapshot.dockerID.rawValue, to: statement, at: 2)
            try connection.bind(generation, to: statement, at: 3)
            try connection.bind(bit, to: statement, at: 4)
            try connection.bind(bit, to: statement, at: 5)
            try connection.done(statement)
            guard sqlite3_changes(connection.database) == 1 else {
                throw SQLiteOutputConnection.failure("Log source EOF has an invalid generation or source")
            }
        }
    }
}
