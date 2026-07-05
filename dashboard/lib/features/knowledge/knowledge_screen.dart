import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api_client.dart';
import '../../core/api_exception.dart';
import '../../core/session.dart';
import 'providers.dart';

class KnowledgeScreen extends ConsumerStatefulWidget {
  const KnowledgeScreen({super.key});

  @override
  ConsumerState<KnowledgeScreen> createState() => _KnowledgeScreenState();
}

class _KnowledgeScreenState extends ConsumerState<KnowledgeScreen> {
  String? _selectedId;
  String? _selectedName;

  @override
  Widget build(BuildContext context) {
    final collections = ref.watch(collectionsProvider);
    return Padding(
      padding: const EdgeInsets.all(16),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 280,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Text('Collections',
                        style: Theme.of(context).textTheme.titleMedium),
                    const Spacer(),
                    IconButton(
                      tooltip: 'New collection',
                      icon: const Icon(Icons.add),
                      onPressed: () => showDialog(
                        context: context,
                        builder: (_) => const _CreateCollectionDialog(),
                      ),
                    ),
                  ],
                ),
                Expanded(
                  child: collections.when(
                    loading: () =>
                        const Center(child: CircularProgressIndicator()),
                    error: (e, _) => Text('Failed: $e'),
                    data: (list) => list.isEmpty
                        ? const Text('No collections yet.')
                        : ListView(
                            children: [
                              for (final c in list)
                                ListTile(
                                  selected: c.id == _selectedId,
                                  leading:
                                      const Icon(Icons.folder_outlined),
                                  title: Text(c.name),
                                  subtitle: Text(c.embeddingBackend),
                                  onTap: () => setState(() {
                                    _selectedId = c.id;
                                    _selectedName = c.name;
                                  }),
                                ),
                            ],
                          ),
                  ),
                ),
              ],
            ),
          ),
          const VerticalDivider(width: 32),
          Expanded(
            child: _selectedId == null
                ? const Center(
                    child: Text('Select a collection to see its documents.'))
                : _CollectionDetail(
                    collectionId: _selectedId!, collectionName: _selectedName!),
          ),
        ],
      ),
    );
  }
}

class _CreateCollectionDialog extends ConsumerStatefulWidget {
  const _CreateCollectionDialog();

  @override
  ConsumerState<_CreateCollectionDialog> createState() =>
      _CreateCollectionDialogState();
}

class _CreateCollectionDialogState
    extends ConsumerState<_CreateCollectionDialog> {
  final _name = TextEditingController();
  final _description = TextEditingController();
  String? _error;

  @override
  void dispose() {
    _name.dispose();
    _description.dispose();
    super.dispose();
  }

  Future<void> _create() async {
    final client = ref.read(activeApiClientProvider);
    if (client == null || _name.text.trim().isEmpty) return;
    try {
      await client.createCollection(
        _name.text.trim(),
        description:
            _description.text.trim().isEmpty ? null : _description.text.trim(),
      );
      ref.invalidate(collectionsProvider);
      if (mounted) Navigator.of(context).pop();
    } on ApiException catch (e) {
      setState(() => _error = e.friendly);
    }
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('New collection'),
      content: SizedBox(
        width: 400,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: _name,
              autofocus: true,
              decoration: const InputDecoration(
                  labelText: 'Name', hintText: 'flutter-docs'),
            ),
            const SizedBox(height: 8),
            TextField(
              controller: _description,
              decoration:
                  const InputDecoration(labelText: 'Description (optional)'),
            ),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.only(top: 12),
                child: Text(_error!,
                    style: TextStyle(color: Theme.of(context).colorScheme.error)),
              ),
          ],
        ),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Cancel')),
        FilledButton(onPressed: _create, child: const Text('Create')),
      ],
    );
  }
}

class _CollectionDetail extends ConsumerStatefulWidget {
  const _CollectionDetail(
      {required this.collectionId, required this.collectionName});

  final String collectionId;
  final String collectionName;

  @override
  ConsumerState<_CollectionDetail> createState() => _CollectionDetailState();
}

class _CollectionDetailState extends ConsumerState<_CollectionDetail> {
  bool _uploading = false;

  Future<void> _upload() async {
    final client = ref.read(activeApiClientProvider);
    if (client == null) return;
    final picked = await FilePicker.platform.pickFiles(withData: true);
    final file = picked?.files.singleOrNull;
    if (file == null || file.bytes == null) return;

    setState(() => _uploading = true);
    try {
      final document = await client.uploadDocument(
          widget.collectionId, file.name, file.bytes!);
      ref.invalidate(documentsProvider(widget.collectionId));
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
            content: Text(document.status == 'embedded'
                ? '${document.filename}: embedded as ${document.chunkCount} chunks'
                : '${document.filename}: ${document.status} — ${document.error}')));
      }
    } on ApiException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(e.friendly)));
      }
    } finally {
      if (mounted) setState(() => _uploading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final documents = ref.watch(documentsProvider(widget.collectionId));
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(widget.collectionName,
                  overflow: TextOverflow.ellipsis,
                  style: Theme.of(context).textTheme.headlineSmall),
            ),
            FilledButton.icon(
              icon: _uploading
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.upload_file),
              label: const Text('Upload document'),
              onPressed: _uploading ? null : _upload,
            ),
          ],
        ),
        const SizedBox(height: 8),
        Expanded(
          flex: 2,
          child: documents.when(
            loading: () => const Center(child: CircularProgressIndicator()),
            error: (e, _) => Text('Failed: $e'),
            data: (list) => list.isEmpty
                ? const Center(child: Text('No documents in this collection.'))
                : ListView(
                    children: [
                      for (final d in list)
                        ListTile(
                          dense: true,
                          leading: Icon(
                            d.status == 'embedded'
                                ? Icons.check_circle_outline
                                : d.status == 'failed'
                                    ? Icons.error_outline
                                    : Icons.hourglass_top,
                            color: d.status == 'embedded'
                                ? Colors.green
                                : d.status == 'failed'
                                    ? Theme.of(context).colorScheme.error
                                    : Colors.orange,
                          ),
                          title: Text(d.filename),
                          subtitle: Text(d.status == 'embedded'
                              ? '${d.chunkCount} chunks · ${(d.sizeBytes / 1024).toStringAsFixed(1)} KB'
                              : d.error ?? d.status),
                        ),
                    ],
                  ),
          ),
        ),
        const Divider(),
        Expanded(flex: 3, child: _Playground(collectionName: widget.collectionName)),
      ],
    );
  }
}

class _Playground extends ConsumerStatefulWidget {
  const _Playground({required this.collectionName});

  final String collectionName;

  @override
  ConsumerState<_Playground> createState() => _PlaygroundState();
}

class _PlaygroundState extends ConsumerState<_Playground> {
  final _query = TextEditingController();
  bool _scopeToCollection = true;
  bool _busy = false;
  List<RetrievedChunkInfo>? _chunks;
  String? _error;

  @override
  void dispose() {
    _query.dispose();
    super.dispose();
  }

  Future<void> _search() async {
    final client = ref.read(activeApiClientProvider);
    if (client == null || _query.text.trim().isEmpty) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final chunks = await client.retrieve(
        _query.text.trim(),
        collection: _scopeToCollection ? widget.collectionName : null,
      );
      setState(() => _chunks = chunks);
    } on ApiException catch (e) {
      setState(() => _error = e.friendly);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('Retrieval playground',
            style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: 8),
        Row(
          children: [
            Expanded(
              child: TextField(
                controller: _query,
                decoration: const InputDecoration(
                    labelText: 'Query', isDense: true),
                onSubmitted: (_) => _search(),
              ),
            ),
            Tooltip(
              message: 'Search only this collection (otherwise the router '
                  'searches all collections)',
              child: Checkbox(
                value: _scopeToCollection,
                onChanged: (v) =>
                    setState(() => _scopeToCollection = v ?? true),
              ),
            ),
            const Text('scoped'),
            const SizedBox(width: 8),
            FilledButton(
              onPressed: _busy ? null : _search,
              child: const Text('Retrieve'),
            ),
          ],
        ),
        if (_error != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(_error!,
                style: TextStyle(color: Theme.of(context).colorScheme.error)),
          ),
        const SizedBox(height: 8),
        Expanded(
          child: _chunks == null
              ? const SizedBox.shrink()
              : _chunks!.isEmpty
                  ? const Text('No results.')
                  : ListView(
                      children: [
                        for (final chunk in _chunks!)
                          Card(
                            child: ListTile(
                              dense: true,
                              leading: Text(chunk.score.toStringAsFixed(2)),
                              title: Text(chunk.text,
                                  maxLines: 3,
                                  overflow: TextOverflow.ellipsis),
                              subtitle: Text(
                                  '${chunk.collection}/${chunk.source}'),
                            ),
                          ),
                      ],
                    ),
        ),
      ],
    );
  }
}
