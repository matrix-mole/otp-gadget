# Related projects

Prior art in the air-gapped handheld encrypted-messaging space, and how OTP Gadget compares. Objective, not promotional.

## Scope

Relevant comparisons are handheld, air-gapped messaging gadgets where encryption happens off-network on a dedicated device.

Explicitly out of scope: hardware password managers such as Mooltipass and OnlyKey, hardware crypto wallets, pure-software airgap setups, and network- or radio-based messengers, except for one included specifically as a contrast.

## Comparison

| Project | Crypto | Key exchange | Transport | Radio? | Smartphone needed? | Forward secrecy | License | Status |
|---|---|---|---|---|---|---|---|---|
| OTP Gadget | One-time pad for message confidentiality; HMAC-SHA256 tag per message; AES-256-CTR at rest for pad material on MicroSD under a PIN-derived key | Physical MicroSD swap; both devices generate random bytes, XOR them into a shared pad, then split send/receive halves | QR code on 3.5" screen, scanned by UART QR scanner; hex hand-typing fallback | No | No | N/A in the public-key sense; pad bytes are single-use by construction | Firmware GPL-3.0; docs/case CC BY-SA 4.0 | Active development; reservation-based built-unit run planned pending EEA compliance |
| Qryptr | Curve25519 ECC + ChaChaPoly authenticated encryption | Public-key device IDs exchanged as QR codes, preferably in person to prevent MITM | QR code on device screen, photographed by smartphone, shared through any messaging app, then scanned by recipient's Qryptr camera | No on device; smartphone provides the network leg | Yes | No | GPL-3.0 | Active commercial product; GitHub mirror inactive, development continued at Codeberg |
| PocketCrypto | GnuPG/GPG based; specific algorithms not documented in repo overview | Not described in repo overview | Text, files on storage media, QR codes, and AFSK-modulated audio for radio | Optional, via AFSK audio over external radio | No | Not documented | GPL-3.0 | Early-stage / minimal activity; no releases |
| CircuitMess Chatter | Not specified in product docs | Not documented | LoRa radio, about 2 km range | Yes | No | Not documented | Not stated on product page | Commercial DIY kit, 159 USD |

## Notes per project

[Qryptr](https://qryptr.com) is a dedicated handheld device with no radio on the device itself. It uses Curve25519 and ChaChaPoly, with public-key device IDs exchanged as QR codes; the project recommends doing that exchange in person to reduce MITM risk. Messages are displayed as QR codes on the Qryptr screen, photographed by a smartphone, sent through any messaging app, and then scanned by the receiving Qryptr camera. Forward secrecy is not implemented; the public TODO references a future pre-shared symmetric key as a quantum-resistance measure, not as PFS. The commercial product is active, while the GitHub mirror is inactive and development has continued at Codeberg.

[PocketCrypto](https://github.com/KBtechnologies/PocketCrypto) is an early-stage GnuPG/GPG-based handheld encryption project. Its overview describes several transports: text for printed or on-screen manual entry, files on storage media, QR codes, and AFSK-modulated audio for use with an external radio. The repo overview does not document the specific GPG algorithms, key-exchange flow, or forward-secrecy properties. It does not require a smartphone. The repo has minimal activity and no releases.

[CircuitMess Chatter](https://circuitmess.com/products/chatter) is a commercial DIY kit for LoRa-based text communication. The product page describes radio transport with about 2 km of range, and does not require a smartphone. Crypto, key exchange, and forward secrecy are not specified in the product docs, and the license is not stated on the product page. It is included here as the contrast case: a handheld messaging gadget whose transport is radio, rather than an air-gapped QR/manual channel.

## Where OTP Gadget sits

OTP Gadget's main cost is operational: establishing a pad requires an in-person MicroSD swap, the pad is finite, and it must be regenerated when exhausted. There is no remote onboarding path.

The benefit is that the pad itself has information-theoretic security when used once per byte, so future compute, including quantum compute, does not break the pad. There is no public-key handshake to attack, no smartphone in the loop at any point, and key material lives only on swappable MicroSD cards encrypted at rest.
