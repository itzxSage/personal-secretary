#if DEBUG && os(iOS)
import Foundation
import Security

/// One-shot developer bootstrap supplied only in the environment of a USB launch.
enum DeveloperRelayPairing {
    private static let service = "com.secretary.relay"

    static func installIfProvided() throws {
        let environment = ProcessInfo.processInfo.environment
        guard environment["LIFEOS_RELAY_PAIRING"] == "1" else { return }
        let allowsRotation = environment["LIFEOS_RELAY_ROTATION"] == "1"
        guard let origin = environment["LIFEOS_RELAY_ORIGIN"].flatMap(URL.init(string:)),
            let conversationID = environment.uuid("LIFEOS_RELAY_CONVERSATION_ID"),
            let participantID = environment.uuid("LIFEOS_RELAY_PARTICIPANT_ID"),
            let deviceID = environment.uuid("LIFEOS_RELAY_DEVICE_ID"),
            let signingKey = environment.data("LIFEOS_RELAY_SIGNING_KEY"), signingKey.count == 32,
            let pkcs12 = environment.data("LIFEOS_RELAY_PKCS12"),
            let password = environment["LIFEOS_RELAY_PKCS12_PASSWORD"]?.data(using: .utf8),
            let fingerprint = environment["LIFEOS_RELAY_SERVER_CERT_SHA256"] else {
            throw RelaySetupError.invalidConfiguration
        }

        let signingAccount = "staging-request-signing"
        let pkcs12Account = "staging-client-pkcs12"
        let passwordAccount = "staging-client-pkcs12-password"
        try store(signingKey, account: signingAccount, allowsRotation: allowsRotation)
        try store(pkcs12, account: pkcs12Account, allowsRotation: allowsRotation)
        try store(password, account: passwordAccount, allowsRotation: allowsRotation)

        let configuration = RelayConnectionConfiguration(
            origin: origin,
            conversationID: conversationID,
            participantID: participantID,
            deviceID: deviceID,
            signingKeyAccount: signingAccount,
            clientPKCS12Account: pkcs12Account,
            clientPKCS12PasswordAccount: passwordAccount,
            serverCertificateSHA256: fingerprint
        )
        let directory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)
            .first!.appendingPathComponent("Secretary", isDirectory: true)
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: [.protectionKey: FileProtectionType.complete]
        )
        try JSONEncoder().encode(configuration).write(
            to: directory.appendingPathComponent("relay.json"),
            options: [.atomic, .completeFileProtection]
        )
    }

    static func writeSmokeTestResult(status: String, diagnostic: String) {
        let result = [
            "status": status,
            "diagnostic": diagnostic,
            "recordedAt": ISO8601DateFormatter().string(from: Date()),
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]),
            let directory = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first
        else { return }
        try? data.write(
            to: directory.appendingPathComponent("lifeos-relay-smoke-result.json"),
            options: [.atomic, .completeFileProtection]
        )
    }

    private static func store(_ data: Data, account: String, allowsRotation: Bool) throws {
        let lookup: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var existing: CFTypeRef?
        let status = SecItemCopyMatching(lookup as CFDictionary, &existing)
        if status == errSecSuccess {
            guard existing as? Data != data else { return }
            guard allowsRotation else { throw RelaySetupError.invalidConfiguration }
            let updateQuery: [String: Any] = [
                kSecClass as String: kSecClassGenericPassword,
                kSecAttrService as String: service,
                kSecAttrAccount as String: account,
            ]
            guard SecItemUpdate(
                updateQuery as CFDictionary,
                [kSecValueData as String: data] as CFDictionary
            ) == errSecSuccess else { throw RelaySetupError.keyUnavailable }
            return
        }
        guard status == errSecItemNotFound else { throw RelaySetupError.keyUnavailable }
        var insertion = lookup
        insertion.removeValue(forKey: kSecReturnData as String)
        insertion.removeValue(forKey: kSecMatchLimit as String)
        insertion[kSecValueData as String] = data
        insertion[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        guard SecItemAdd(insertion as CFDictionary, nil) == errSecSuccess else {
            throw RelaySetupError.keyUnavailable
        }
    }
}

private extension Dictionary where Key == String, Value == String {
    func uuid(_ name: String) -> UUID? {
        self[name].flatMap(UUID.init(uuidString:))
    }

    func data(_ name: String) -> Data? {
        self[name].flatMap { Data(base64Encoded: $0) }
    }
}
#endif
