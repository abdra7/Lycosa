import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api_client.dart';
import '../../core/session.dart';

final collectionsProvider =
    FutureProvider.autoDispose<List<CollectionInfo>>((ref) async {
  final client = ref.watch(activeApiClientProvider);
  if (client == null) return const [];
  return client.listCollections();
});

final documentsProvider = FutureProvider.autoDispose
    .family<List<DocumentInfo>, String>((ref, collectionId) async {
  final client = ref.watch(activeApiClientProvider);
  if (client == null) return const [];
  return client.listDocuments(collectionId);
});
